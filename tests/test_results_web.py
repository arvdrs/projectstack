"""
Results analysis in the web app: JTL upload → background job → run details,
observations, reports, ask, delete — and analysing a test run started from the
app (record → generate → run in JMeter → analyse that session's results).
"""

import json
import secrets
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from projectstack.api import main as api
from projectstack.api import results_api
from projectstack.runner.jmeter_runner import find_jmeter

from tests.har_recorder import record_checkout_flow
from tests.jtl_synth import START_MS, ramp_steady, sample, scenario_csv, write_csv
from tests.test_e2e_replay import server_url  # noqa: F401  (fixture)

SLA = json.dumps({"response_time_metric": "p95", "response_time_ms": 1000, "error_rate_pct": 5})


@pytest.fixture(scope="module")
def ch_db():
    from projectstack.config import load_settings
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    name = f"pstack_test_{secrets.token_hex(4)}"
    try:
        db = ResultsDB(ClickHouseConfig.from_settings({**load_settings(), "clickhouse_database": name}))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    yield name
    db.command(f"DROP DATABASE IF EXISTS {name}")


@pytest.fixture
def client(ch_db, tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", ch_db)
    monkeypatch.setenv("PSTACK_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(results_api, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api, "_SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(api, "REPORTS_BASE_DIR", tmp_path / "reports")
    return TestClient(api.app)


def _wait(client, job_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/results/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.2)
    raise AssertionError("analysis job did not finish")


def test_upload_analyse_and_read(client, tmp_path):
    assert client.get("/api/results/status").json()["available"] is True
    path = scenario_csv(tmp_path / "web.jtl", ramp_steady(users=20, ramp_s=120, steady_s=600, down_s=60), with_tc=True,
                        latency_ms=lambda s, u: 200.0 if s < 6 * 60 else 600.0)
    with path.open("rb") as f:
        res = client.post("/api/results/analyze", files={"file": ("web.jtl", f)},
                          data={"scenario": "web", "tag": "b1", "sla": SLA})
    assert res.status_code == 200, res.text
    job = _wait(client, res.json()["id"])
    assert job["status"] == "done", job
    assert any("web.jtl" in p for p in job["progress"])                     # the user's file name, not a temp name
    assert not list((tmp_path / "uploads").iterdir())                       # upload removed once loaded

    run = client.get(f"/api/results/runs/{job['run_id']}").json()
    assert run["run"]["scenario"] == "web" and run["source_file"] == "web.jtl"
    assert run["verdict"] in ("fail", "at_risk", "pass")
    assert [f["n"] for f in run["findings"]] == list(range(1, len(run["findings"]) + 1))
    assert any(f["type"] == "latency_change" for f in run["findings"])
    assert run["observations"]["headline"] and run["observations"]["key_points"]
    assert run["observations_ai"] is None and run["sla"]["defined"]

    listed = client.get("/api/results/runs").json()["runs"]
    assert listed[0]["run_id"] == job["run_id"] and listed[0]["created_at"].endswith("+00:00")

    html = client.get(f"/api/results/runs/{job['run_id']}/report?audience=tech")
    assert html.status_code == 200 and "<svg" in html.text and "nothing was sent to an AI service" in html.text
    md = client.get(f"/api/results/runs/{job['run_id']}/report?audience=exec&format=md&download=true")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    folder = tmp_path / "runs" / job["run_id"]
    assert (folder / "web" / "report_tech.html").exists() and not (folder / "report_tech.html").exists()

    # A second upload of the same scenario can be compared with the first
    with path.open("rb") as f:
        second = client.post("/api/results/analyze", files={"file": ("web2.jtl", f)},
                             data={"scenario": "web", "tag": "b2", "baseline": job["run_id"]}).json()
    second = _wait(client, second["id"])
    detail = client.get(f"/api/results/runs/{second['run_id']}").json()
    assert detail["baseline"]["found"] and detail["baseline"]["run_id"] == job["run_id"]
    cmp = client.get(f"/api/results/compare?base={job['run_id']}&current={second['run_id']}").json()
    assert cmp["load"]["comparable"] and cmp["summary"]["regressed"] == 0

    assert client.delete(f"/api/results/runs/{second['run_id']}").json() == {"deleted": second["run_id"]}
    assert client.get(f"/api/results/runs/{second['run_id']}").status_code == 404


def test_tiny_run_and_bad_uploads(client, tmp_path):
    rows = sorted([sample(START_MS + i * 700, 100 + i, "GET /home", "TG 1-1", 1) for i in range(6)]
                  + [sample(START_MS + 300 + i * 700, 80, "POST /login", "TG 1-1", 1, ok=i != 3,
                            code="500" if i == 3 else "200") for i in range(6)], key=lambda r: r["timeStamp"])
    path = write_csv(tmp_path / "tiny.jtl", rows)
    with path.open("rb") as f:
        job = _wait(client, client.post("/api/results/analyze", files={"file": ("tiny.jtl", f)}).json()["id"])
    assert job["status"] == "done", job                                      # a one-user, few-second app run
    run = client.get(f"/api/results/runs/{job['run_id']}").json()
    assert run["verdict"] == "no_sla" and run["run"]["samples"] == 12

    empty = client.post("/api/results/analyze", files={"file": ("empty.jtl", b"")})
    assert empty.status_code == 400
    bad = client.post("/api/results/analyze", files={"file": ("notes.jtl", b"this is not a result file\n")}).json()
    bad = _wait(client, bad["id"])
    assert bad["status"] == "failed" and bad["error"]
    assert client.get("/api/results/jobs/nope").status_code == 404
    assert client.get("/api/results/runs/2099").status_code == 404


def test_ai_endpoints_without_provider(client, tmp_path, monkeypatch):
    from projectstack.llm import registry
    from projectstack.llm.base import LLMConfigError
    path = write_csv(tmp_path / "t.jtl", [sample(START_MS + i * 1000, 100, "GET /a", "T 1-1", 1) for i in range(30)])
    with path.open("rb") as f:
        run_id = _wait(client, client.post("/api/results/analyze", files={"file": ("t.jtl", f)}).json()["id"])["run_id"]

    def no_key(task, settings, override=None):
        raise LLMConfigError("No Anthropic API key")
    monkeypatch.setattr(registry, "provider_for_task", no_key)
    obs = client.post(f"/api/results/runs/{run_id}/observations", json={}).json()
    assert obs["source"] == "rules" and "No Anthropic API key" in obs["note"]      # falls back, says why
    ask = client.post(f"/api/results/runs/{run_id}/ask", json={"question": "How many samples?"})
    assert ask.status_code == 400 and "AI provider" in ask.json()["detail"]


def test_status_when_database_is_down(client, monkeypatch):
    monkeypatch.setenv("PSTACK_CLICKHOUSE_PORT", "1")
    status = client.get("/api/results/status").json()
    assert status["available"] is False and "docker compose" in status["hint"]
    assert client.get("/api/results/runs").status_code == 503


@pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")
def test_analyse_a_test_run_from_the_app(client, server_url, tmp_path):  # noqa: F811
    har = tmp_path / "flow.har"
    record_checkout_flow(server_url, har)
    with har.open("rb") as f:
        session_id = client.post("/api/upload", files={"file": ("flow.har", f, "application/json")}).json()["session_id"]
    assert client.post(f"/api/correlate/{session_id}", json={}).status_code == 200
    assert client.post(f"/api/generate/{session_id}", json={"include_view_tree": False, "test_name": "Checkout flow"}).status_code == 200

    early = client.post(f"/api/results/analyze-session/{session_id}", json={})
    assert early.status_code == 400 and "run the test first" in early.json()["detail"]

    run = client.post(f"/api/run/{session_id}", json={"threads": 2, "loops": 3, "sla": {
        "response_time_metric": "p95", "response_time_ms": 60000, "error_rate_pct": 50}})
    assert run.status_code == 200, run.text

    job = client.post(f"/api/results/analyze-session/{session_id}", json={}).json()
    assert job["session_id"] == session_id
    job = _wait(client, job["id"], timeout=120)
    assert job["status"] == "done", job
    detail = client.get(f"/api/results/runs/{job['run_id']}").json()
    assert detail["run"]["scenario"] == "Checkout flow" and detail["run"]["tag"] == "app run"
    assert detail["sla"]["defined"]                                           # the SLA the run used
    assert detail["has_plan"]                                                 # the generated JMX, for correlation mapping
    assert detail["baseline"]["requested"] == "last-green" and not detail["baseline"]["found"]
    assert detail["observations"]["headline"]

    # The next run of the same script is compared with this one once it passed
    if detail["verdict"] == "pass":
        assert client.post(f"/api/run/{session_id}", json={"threads": 2, "loops": 3, "sla": {
            "response_time_metric": "p95", "response_time_ms": 60000, "error_rate_pct": 50}}).status_code == 200
        second = _wait(client, client.post(f"/api/results/analyze-session/{session_id}", json={}).json()["id"], 120)
        later = client.get(f"/api/results/runs/{second['run_id']}").json()
        assert later["baseline"]["found"] and later["baseline"]["run_id"] == job["run_id"]
