"""
Two recordings of the same journey: requests are paired by step (not position),
values identical in both are flagged as static, and the auto-correlation result
gets an overall confidence assessment.
"""

import copy
import json

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from projectstack.api import main as api
from projectstack.correlator.confidence import assess
from projectstack.correlator.engine import pair_requests, request_signature, run_correlation_with_comparison
from projectstack.parser.har_parser import HarRequest, parse_har

from tests.har_recorder import record_checkout_flow
from tests.test_e2e_replay import server_url  # noqa: F401  (fixture)


def _req(url, method="GET", body="", headers=None):
    return HarRequest(method=method, url=url, headers=headers or {}, query_params=[], body=None, body_mime_type=None,
                      status_code=200, response_headers={}, response_body=body, response_mime_type="text/html", cookies=[])


@pytest.fixture
def two_runs(server_url, tmp_path):  # noqa: F811
    h1, h2 = tmp_path / "run1.har", tmp_path / "run2.har"
    record_checkout_flow(server_url, h1)
    record_checkout_flow(server_url, h2)
    return h1, h2


def test_requests_pair_by_step_not_position():
    a = [_req("https://s.test/actions/Catalog.action"), _req("https://s.test/api/orders/123"),
         _req("https://s.test/actions/Catalog.action?viewProduct=&productId=FI-SW-01")]
    b = [_req("https://s.test/actions/Catalog.action"), _req("https://s.test/poll"),          # extra request
         _req("https://s.test/api/orders/456"), _req("https://s.test/actions/Catalog.action?viewProduct=&productId=K9-BD-01")]
    assert request_signature(a[1]) == request_signature(b[2])
    assert pair_requests(a, b) == {0: 0, 1: 2, 2: 3}


def test_second_run_confirms_what_changes(two_runs):
    r1, r2 = parse_har(two_runs[0]), parse_har(two_runs[1])
    r2.insert(2, copy.deepcopy(r2[3]))              # a retry in the second recording
    final, _, report = run_correlation_with_comparison(r1, r2)
    assert report["paired"] == len(r1)
    assert all("confirmed_dynamic" in cv.detection_pass for cv in final)
    a = assess(final, r1, r2, report)
    assert a["label"] == "High" and a["counts"]["confirmed"] == len(final)


def test_identical_recordings_prove_nothing():
    page = ('<form><input type="hidden" name="_sourcePage" value="pfINKaVVek8YcLdKTN5rNa_skoVRs9m7mfrUsC6lfk"/>'
            '<input name="order.billState" value="Alaska"/></form>')
    reqs = [_req("https://s.test/actions/Order.action?newOrderForm=", body=page),
            _req("https://s.test/actions/Order.action", method="POST",
                 headers={"Content-Type": "application/x-www-form-urlencoded"})]
    reqs[1].body = "_sourcePage=pfINKaVVek8YcLdKTN5rNa_skoVRs9m7mfrUsC6lfk&order.billState=Alaska"
    final, _, report = run_correlation_with_comparison(reqs, copy.deepcopy(reqs))
    passes = {cv.extract_value: cv.detection_pass for cv in final}
    assert "same_in_both" in passes["pfINKaVVek8YcLdKTN5rNa_skoVRs9m7mfrUsC6lfk"]   # token: kept, flagged
    assert "static_in_both" in passes["Alaska"]                                     # static: switched off
    a = assess(final, reqs, copy.deepcopy(reqs), report)
    assert a["label"] != "High"
    assert any("same recording uploaded twice" in f["text"] for f in a["factors"])


def test_untraced_values_lower_confidence():
    reqs = [_req("https://s.test/"), _req("https://s.test/api/save", headers={"X-Trace": "a91bc77de03f"})]
    alone = assess([], reqs)
    assert any("could not be traced" in f["text"] for f in alone["factors"])
    other = copy.deepcopy(reqs)
    other[1].headers["X-Trace"] = "5f0e21aa9c13"                                   # changes between runs
    with_second = assess([], reqs, other, {"paired": 2})
    assert any("changed between the recordings" in f["text"] for f in with_second["factors"])
    assert with_second["score"] < alone["score"]


def test_compare_endpoint_filters_the_second_recording(two_runs, tmp_path, monkeypatch, server_url):  # noqa: F811
    monkeypatch.setattr(api, "_SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    client = TestClient(api.app)
    h1, h2 = two_runs
    data = json.loads(h2.read_text())
    noise = copy.deepcopy(data["log"]["entries"][0])
    noise["request"]["url"] = "https://cdn.other.test/widget/config"
    data["log"]["entries"].insert(1, noise)                    # another domain, only in the second recording
    h2.write_text(json.dumps(data))

    sid = client.post("/api/upload", files={"file": ("a.har", h1.read_bytes(), "application/json")}).json()["session_id"]
    sid2 = client.post("/api/upload", files={"file": ("b.har", h2.read_bytes(), "application/json")}).json()["session_id"]
    host = server_url.split("//")[1]
    assert client.post(f"/api/filter/{sid}", params={"base_url": host}).status_code == 200

    res = client.post(f"/api/correlate-compare/{sid}", json={"second_session_id": sid2}).json()
    assert res["comparison"]["second_total"] == res["comparison"]["total"]     # other domain filtered out
    assert res["comparison"]["paired"] == res["comparison"]["total"]
    assert res["assessment"]["label"] in ("High", "Medium", "Low")
    assert all(v["extract_value_2"] for v in res["values"])

    single = client.post(f"/api/correlate/{sid}", json={}).json()
    assert any("Not cross-checked" in f["text"] for f in single["assessment"]["factors"])
