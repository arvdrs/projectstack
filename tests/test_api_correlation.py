"""API flow: upload → correlate → edit (rename) → generate, plus rules and replay endpoints."""

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from projectstack.api import main as api
from projectstack.runner.jmeter_runner import find_jmeter

from tests.test_e2e_replay import server_url  # noqa: F401  (fixture)
from tests.har_recorder import record_checkout_flow


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Keep sessions/reports out of the user's home directory
    monkeypatch.setattr(api, "_SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(api, "REPORTS_BASE_DIR", tmp_path / "reports")
    return TestClient(api.app)


@pytest.fixture
def session_id(client, server_url, tmp_path):  # noqa: F811
    har = tmp_path / "flow.har"
    record_checkout_flow(server_url, har)
    with open(har, "rb") as f:
        res = client.post("/api/upload", files={"file": ("flow.har", f, "application/json")})
    assert res.status_code == 200
    return res.json()["session_id"]


def test_renamed_and_custom_correlations_reach_the_jmx(client, session_id):
    res = client.post(f"/api/correlate/{session_id}", json={"use_ai": False})
    values = res.json()["values"]
    assert all("confidence_score" in v and "extractor" in v for v in values)

    bearer = next(v for v in values if v["var_name"] == "C_BEARER_TOKEN")
    bearer["var_name"] = "AUTH_TOKEN"
    kept = [v for v in values if v["var_name"] != "C_ORDER_NO"]   # user disabled one row

    custom = client.post(f"/api/apply-rule/{session_id}", json={
        "var_name": "C_TOTAL_STATUS", "source_request_index": 4, "extract_from": "body",
        "extract_type": "begin_end", "extract_begin": '"status":"', "extract_end": '"',
    })
    assert custom.status_code == 200
    assert custom.json()["extractor"] == "boundary"
    assert all(u > 4 for u in custom.json()["used_in_requests"])

    saved = client.put(f"/api/correlations/{session_id}", json={"values": kept + [custom.json()]})
    assert saved.status_code == 200, saved.text

    gen = client.post(f"/api/generate/{session_id}", json={"selected_var_names": None})
    jmx = gen.json()["jmx"]
    assert "${AUTH_TOKEN}" in jmx and "Extract AUTH_TOKEN" in jmx
    assert "C_BEARER_TOKEN" not in jmx
    assert "Extract C_ORDER_NO" not in jmx
    assert "C_TOTAL_STATUS" in jmx


def test_save_correlations_rejects_bad_names(client, session_id):
    bad = {"var_name": "has space", "source_request_index": 0, "extract_from": "body",
           "extract_key": "k", "extract_value": "v", "used_in_requests": [1]}
    assert client.put(f"/api/correlations/{session_id}", json={"values": [bad]}).status_code == 400


def test_rule_pack_endpoints(client, tmp_path, monkeypatch):
    from projectstack.correlator import rule_packs
    monkeypatch.setattr(rule_packs, "USER_DIR", tmp_path / "rules")
    packs = client.get("/api/rule-packs").json()["packs"]
    assert any(p["id"] == "aspnet" for p in packs)
    pack = {"id": "acme", "name": "Acme", "rules": [
        {"id": "r1", "name": "token", "type": "regex", "regex": "tok=(\\w+)", "var": "C_ACME"}]}
    assert client.post("/api/rule-packs", json=pack).status_code == 200
    assert any(p["id"] == "acme" for p in client.get("/api/rule-packs").json()["packs"])
    assert client.post("/api/rule-packs", json={"id": "x", "rules": [{"type": "nope"}]}).status_code == 400
    assert client.delete("/api/rule-packs/acme").status_code == 200
    assert client.delete("/api/rule-packs/aspnet").status_code == 404


@pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")
def test_replay_fix_endpoint(client, session_id):
    client.post(f"/api/correlate/{session_id}", json={"use_ai": False})
    res = client.post(f"/api/replay-fix/{session_id}", json={"max_iterations": 2})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["final"]["failed"] == 0
    assert body["iterations"][0]["verified_count"] >= 6


@pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")
def test_run_uses_script_settings_and_reruns_are_fresh(client, session_id):
    client.post(f"/api/correlate/{session_id}", json={"use_ai": False})
    gen = client.post(f"/api/generate/{session_id}", json={"threads": 2, "rampup": 1, "loops": 3})
    assert gen.json()["load_config"] == {"threads": 2, "rampup": 1, "loops": 3, "duration_seconds": 0}

    first = client.post(f"/api/run/{session_id}", json={}).json()       # no overrides → script settings
    assert first["run_config"]["threads"] == 2
    assert first["total_requests"] == 6 * 2 * 3

    second = client.post(f"/api/run/{session_id}", json={"threads": 1, "loops": 1}).json()
    assert second["total_requests"] == 6, "a re-run must not report the previous run's results"

    bad = client.post(f"/api/run/{session_id}", json={"loops": -1, "duration_seconds": 0})
    assert bad.status_code == 400


@pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")
def test_run_live_reports_the_finished_run(client, session_id):
    client.post(f"/api/correlate/{session_id}", json={"use_ai": False})
    client.post(f"/api/generate/{session_id}", json={"threads": 2, "rampup": 1, "loops": 2})
    assert client.get(f"/api/run-live/{session_id}").status_code == 404
    run = client.post(f"/api/run/{session_id}", json={}).json()
    live = client.get(f"/api/run-live/{session_id}").json()
    assert live["status"] == "done"
    assert live["totals"]["requests"] == run["total_requests"] == live["expected_requests"] == 24
    assert {l["label"] for l in live["labels"]} >= {"GET /login", "GET /api/receipt/{C_ORDER_NO}"}
    assert client.post(f"/api/run-stop/{session_id}").status_code == 400   # nothing running
