"""
Workflow features: rule-based vs AI auto-correlation, user-defined transactions,
thread-group types (Standard / Stepping / Concurrency / Ultimate) and optional SLAs.
"""

import re
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from projectstack.analyzer.report_analyzer import derive_insights, from_session_results
from projectstack.analyzer.sla import evaluate_sla, normalize_sla
from projectstack.api import main as api
from projectstack.generator.jmx_generator import generate_jmx
from projectstack.generator.transaction_grouper import clean_transaction_name
from projectstack.parser.har_parser import parse_har
from projectstack.runner import jmeter_runner
from projectstack.runner.jmeter_runner import (
    find_jmeter, jmeter_info, profile_summary, read_thread_group, read_thread_settings, required_plugin, run_jmeter,
)
from projectstack.runner.metrics_parser import parse_jtl

from tests.har_recorder import record_checkout_flow
from tests.test_e2e_replay import server_url  # noqa: F401  (fixture)

needs_jmeter = pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")
needs_plugins = pytest.mark.skipif(not jmeter_info()["custom_thread_groups"],
                                   reason="JMeter Plugins (Custom Thread Groups) not installed")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(api, "REPORTS_BASE_DIR", tmp_path / "reports")
    return TestClient(api.app)


@pytest.fixture
def har(server_url, tmp_path):  # noqa: F811
    path = tmp_path / "flow.har"
    record_checkout_flow(server_url, path)
    return path


@pytest.fixture
def session_id(client, har):
    with open(har, "rb") as f:
        res = client.post("/api/upload", files={"file": ("flow.har", f, "application/json")})
    assert res.status_code == 200
    return res.json()["session_id"]


def _sampler_names(jmx: str) -> list[str]:
    return re.findall(r'<HTTPSamplerProxy [^>]*testname="([^"]+)"', jmx)


# ---------------------------------------------------------------------------
# SLA
# ---------------------------------------------------------------------------

def test_no_sla_by_default():
    assert normalize_sla(None) is None
    assert normalize_sla({}) is None
    # A metric choice alone is not a target
    assert normalize_sla({"response_time_metric": "p95", "transaction_targets": {"Login": None}}) is None
    assert evaluate_sla(None, {"GET /": {"total": 1}}, 1.0) == {"defined": False}


def test_sla_rejects_nonsense_targets():
    with pytest.raises(ValueError):
        normalize_sla({"response_time_ms": 0})
    with pytest.raises(ValueError):
        normalize_sla({"error_rate_pct": 150})
    with pytest.raises(ValueError):
        normalize_sla({"response_time_metric": "p50", "response_time_ms": 100})


def test_sla_checks_transactions_and_overrides():
    by_label = {
        "TC_01_Login": {"total": 10, "failed": 0, "p90_ms": 1800},
        "TC_02_Add_to_cart": {"total": 10, "failed": 0, "p90_ms": 900},
        "01_01_Login": {"total": 10, "failed": 1, "p90_ms": 1700},
        "02_01_Cart": {"total": 10, "failed": 0, "p90_ms": 800},
    }
    sla = normalize_sla({"response_time_metric": "p90", "response_time_ms": 1000,
                         "transaction_targets": {"Login": 2000, "Checkout": 500},
                         "error_rate_pct": 1, "throughput_rps": 1})
    res = evaluate_sla(sla, by_label, duration_sec=10)
    checks = {(c["kind"], c["label"]): c for c in res["checks"]}
    # Response time applies to transactions (not their child samplers); Login uses its override
    assert checks[("response_time", "TC_01_Login")]["target"] == 2000
    assert checks[("response_time", "TC_01_Login")]["passed"]
    assert checks[("response_time", "TC_02_Add_to_cart")]["passed"]
    assert ("response_time", "01_01_Login") not in checks
    # A target for a transaction that never ran cannot pass
    assert checks[("response_time", "Checkout")]["passed"] is False
    # Error rate counts requests only: 1 failure of 20 = 5%
    assert checks[("error_rate", None)]["actual"] == 5.0 and not checks[("error_rate", None)]["passed"]
    assert checks[("throughput", None)]["actual"] == 2.0 and checks[("throughput", None)]["passed"]
    assert res["defined"] and not res["passed"] and res["failed_count"] == 2


def _results(p95=3000, failed=0):
    return {"total_requests": 10, "passed": 10 - failed, "failed": failed, "error_rate": failed * 10.0,
            "avg_ms": p95 / 2, "min_ms": 1, "max_ms": p95, "p90_ms": p95, "p95_ms": p95, "throughput": 5,
            "by_label": {"GET /a": {"total": 10, "failed": failed, "error_rate": failed * 10.0,
                                    "avg_ms": p95 / 2, "p90_ms": p95, "p95_ms": p95, "p99_ms": p95}}}


def test_report_without_sla_is_not_judged():
    report = from_session_results(_results(p95=9000))
    report["apdex"] = {"overall": [0.2, 500, 1500], "items": []}   # JMeter's default thresholds
    out = derive_insights(report)
    assert out["verdict"] == "no_sla" and out["score"] is None
    assert out["kpis"]["apdex"] is None
    assert not any(f["severity"] == "critical" for f in out["findings"])
    assert not any("threshold" in f["detail"] or "target" in f["title"].lower() for f in out["findings"])
    assert all(t["status"] == "none" for t in out["transactions"])


def test_report_with_sla_is_judged_only_against_it():
    results = _results(p95=900)
    sla = evaluate_sla(normalize_sla({"response_time_metric": "p95", "response_time_ms": 1000}),
                       results["by_label"], 2.0)
    out = derive_insights(from_session_results(results), sla)
    assert out["verdict"] == "pass" and out["score"] == 100
    assert out["transactions"][0]["status"] == "ok"

    sla = evaluate_sla(normalize_sla({"response_time_metric": "p95", "response_time_ms": 500}),
                       results["by_label"], 2.0)
    out = derive_insights(from_session_results(results), sla)
    assert out["verdict"] == "fail"
    assert any(f["title"].startswith("SLA missed") for f in out["findings"])


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def test_transaction_names_are_cleaned():
    assert clean_transaction_name("TC_03_Login") == "Login"
    assert clean_transaction_name("T01_SearchProduct") == "SearchProduct"
    assert clean_transaction_name("Add to cart") == "Add_to_cart"
    assert clean_transaction_name("Teams_page") == "Teams_page"


def test_user_transactions_follow_request_order(client, session_id):
    n = len(api._sessions[session_id]["requests"])
    order = list(reversed(range(n)))
    names = {str(i): ("Browse" if i >= 3 else "Sign in") for i in range(n)}
    res = client.post(f"/api/generate/{session_id}", json={
        "no_transactions": False, "custom_transaction_names": names, "request_order": order,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert [t["name"] for t in body["transactions"]] == ["TC_01_Browse", "TC_02_Sign_in"]
    jmx = body["jmx"]
    assert jmx.index('testname="TC_01_Browse"') < jmx.index('testname="TC_02_Sign_in"')
    # Samplers are numbered per transaction and keep the user's order
    assert _sampler_names(jmx)[0].startswith("01_01_")


def test_auto_grouping_uses_request_order(client, session_id):
    n = len(api._sessions[session_id]["requests"])
    order = list(reversed(range(n)))
    res = client.post(f"/api/auto-transactions/{session_id}", json={"request_order": order})
    assert res.status_code == 200
    names = res.json()["transaction_names"]
    assert set(names) == {str(i) for i in range(n)}
    assert all(not v.startswith("TC_") for v in names.values())

    # With grouping on and no names, generation groups automatically in the same order
    flat = client.post(f"/api/generate/{session_id}", json={"request_order": order}).json()["jmx"]
    grouped = client.post(f"/api/generate/{session_id}", json={"no_transactions": False, "request_order": order}).json()
    paths = lambda jmx: re.findall(r'name="HTTPSampler.path">([^<]*)<', jmx)  # noqa: E731
    assert paths(grouped["jmx"]) == paths(flat)
    assert grouped["transaction_count"] >= 1


# ---------------------------------------------------------------------------
# Auto-correlation (rule-based) vs AI auto-correlation
# ---------------------------------------------------------------------------

def test_auto_correlation_never_calls_ai(client, session_id, monkeypatch):
    from projectstack.correlator import llm_router
    monkeypatch.setattr(llm_router, "detect_with_provider",
                        lambda *a, **k: pytest.fail("rule-based correlation called the AI provider"))
    res = client.post(f"/api/correlate/{session_id}", json={})
    assert res.status_code == 200 and res.json()["count"] > 0
    assert not any("pass6" in v["detection_pass"] for v in res.json()["values"])


def test_ai_correlation_adds_on_top(client, session_id, monkeypatch):
    from projectstack.correlator import llm_router
    reqs = api._sessions[session_id]["requests"]
    src = next(i for i, r in enumerate(reqs) if "access_token" in (r.response_body or ""))
    token = re.search(r'"access_token"\s*:\s*"([^"]+)"', reqs[src].response_body).group(1)

    # The user kept only one rule-based correlation; AI must add the token back, not replace the list
    auto = client.post(f"/api/correlate/{session_id}", json={}).json()["values"]
    keep = [v for v in auto if v["extract_value"] != token][:1]
    assert client.put(f"/api/correlations/{session_id}", json={"values": keep}).status_code == 200

    monkeypatch.setattr(llm_router, "detect_with_provider", lambda requests, settings, strict=False: [
        {"source_request_index": src, "field_name": keep[0]["var_name"], "location": "body",
         "value": token, "used_in_request_indices": [], "reason": "bearer token"},
    ])
    res = client.post(f"/api/ai-correlate/{session_id}", json={"provider": "groq"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["added"]) == 1 and body["count"] == 2
    added = body["added"][0]
    assert added["extract_value"] == token and added["used_in_requests"]
    assert "pass6" in added["detection_pass"]
    assert added["var_name"] != keep[0]["var_name"]   # renamed instead of clashing


def test_ai_correlation_reports_provider_errors(client, session_id, monkeypatch):
    monkeypatch.setitem(api._settings, "groq_api_key", "")
    res = client.post(f"/api/ai-correlate/{session_id}", json={"provider": "groq"})
    assert res.status_code == 400 and "API key" in res.json()["detail"]
    assert client.post(f"/api/ai-correlate/{session_id}", json={"provider": "nope"}).status_code == 400


# ---------------------------------------------------------------------------
# JMeter path from Settings
# ---------------------------------------------------------------------------

def test_configured_jmeter_path_wins(tmp_path, monkeypatch):
    home = tmp_path / "apache-jmeter"
    (home / "bin").mkdir(parents=True)
    (home / "lib" / "ext").mkdir(parents=True)
    exe = home / "bin" / "jmeter"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    (home / "lib" / "ext" / "jmeter-plugins-casutg-2.10.jar").write_text("")
    monkeypatch.setattr(jmeter_runner, "_configured_path", None)
    for value in (str(exe), str(home / "bin"), str(home)):
        jmeter_runner.set_jmeter_path(value)
        assert find_jmeter() == str(exe)
        info = jmeter_info()
        assert info["configured_valid"] and info["custom_thread_groups"]
    jmeter_runner.set_jmeter_path("jmeter")          # the Settings default means "search as usual"
    assert jmeter_runner._configured_path is None


# ---------------------------------------------------------------------------
# Thread groups
# ---------------------------------------------------------------------------

THREAD_GROUPS = {
    "stepping": {"type": "stepping", "threads": 3, "initial_delay": 0, "start_burst": 1, "step_users": 1,
                 "step_period": 1, "step_rampup": 0, "hold": 2, "stop_users": 3, "stop_period": 1},
    "concurrency": {"type": "concurrency", "target": 2, "ramp_up": 2, "steps": 2, "hold": 2, "iterations": 0},
    "ultimate": {"type": "ultimate", "rows": [
        {"threads": 2, "initial_delay": 0, "startup": 1, "hold": 2, "shutdown": 0},
        {"threads": 1, "initial_delay": 1, "startup": 0, "hold": 1, "shutdown": 0},
    ]},
}


@pytest.mark.parametrize("kind", THREAD_GROUPS)
def test_plugin_thread_group_round_trip(har, tmp_path, kind):
    reqs = parse_har(har)
    jmx = tmp_path / f"{kind}.jmx"
    generate_jmx(reqs, [], output_path=str(jmx), thread_group=THREAD_GROUPS[kind])
    ET.parse(jmx)                                    # well-formed
    assert required_plugin(str(jmx)) == kind
    back = read_thread_group(str(jmx))
    for key, value in THREAD_GROUPS[kind].items():
        assert back[key] == value, key
    settings = read_thread_settings(str(jmx))
    assert settings["loops"] == -1 and settings["duration_seconds"] > 0
    assert settings["threads"] == profile_summary(kind, THREAD_GROUPS[kind])["peak"]


def test_profile_summary():
    assert profile_summary("concurrency", {"target": 50, "ramp_up": 60, "hold": 120}) == \
        {"peak": 50, "to_peak": 60, "duration": 180}
    # 10 users, 2 at a time every 10 s with 0 s ramp: 4 more steps after the first batch
    s = profile_summary("stepping", {"threads": 10, "step_users": 2, "step_period": 10, "step_rampup": 0,
                                     "hold": 30, "stop_users": 10, "stop_period": 5})
    assert s == {"peak": 10, "to_peak": 40, "duration": 75}
    assert profile_summary("ultimate", THREAD_GROUPS["ultimate"])["peak"] == 3


@needs_jmeter
@needs_plugins
@pytest.mark.parametrize("kind", THREAD_GROUPS)
def test_plugin_thread_groups_run_in_jmeter(har, tmp_path, kind):
    reqs = parse_har(har)
    jmx = tmp_path / f"{kind}.jmx"
    generate_jmx(reqs, [], output_path=str(jmx), thread_group=THREAD_GROUPS[kind], include_view_tree=False)
    out = run_jmeter(str(jmx), output_dir=tmp_path / kind, timeout=120)
    log = (tmp_path / kind / "jmeter.log").read_text(errors="replace")
    assert "CannotResolveClassException" not in log and "ConversionException" not in log, log[-2000:]
    summary = parse_jtl(out["jtl_path"])
    assert summary.total_requests >= len(reqs), out["stdout"][-1000:]


@needs_jmeter
def test_run_reports_sla_only_when_defined(client, session_id):
    assert client.post(f"/api/generate/{session_id}", json={"include_view_tree": False}).status_code == 200

    plain = client.post(f"/api/run/{session_id}", json={}).json()
    assert plain["sla"] == {"defined": False}
    insights = client.get(f"/api/report-insights/{session_id}").json()
    assert insights["verdict"] == "no_sla" and insights["score"] is None and insights["kpis"]["apdex"] is None

    res = client.post(f"/api/run/{session_id}", json={"sla": {
        "response_time_metric": "p95", "response_time_ms": 60000, "error_rate_pct": 100,
    }})
    assert res.status_code == 200, res.text
    sla = res.json()["sla"]
    assert sla["defined"] and sla["passed"] and sla["checks"]
    insights = client.get(f"/api/report-insights/{session_id}").json()
    assert insights["verdict"] == "pass" and insights["sla"]["defined"]

    bad = client.post(f"/api/run/{session_id}", json={"sla": {"response_time_ms": -5}})
    assert bad.status_code == 400
