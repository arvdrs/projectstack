"""
Results analysis in the web app: JTL upload → background job → run details,
observations, reports, ask, delete — and analysing a test run started from the
app (record → generate → run in JMeter → analyse that session's results).
"""

import json
from pathlib import Path
import re
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

    # Performance Highlights & Observations, then a test context that changes it
    h = run["highlights"]
    assert h["intro"][0].startswith("The test was executed on ") and "20 concurrent users." in h["intro"][1]
    assert "ramp-up followed by" in h["intro"][2]
    assert [s["name"] for s in h["stats"]] == ["Maximum users", "Total requests", "Average throughput", "Total errors"]
    assert h["observations"][0].startswith("Executed ") and run["highlights_ai"] is None
    assert run["context"]["source"] == "none"
    assert all(r["run_samples"] >= r["samples"] for r in run["labels"]["request"])     # whole run vs steady state
    saved = client.put(f"/api/results/runs/{job['run_id']}/context",
                       json={"region": "us-east-1", "test_type": "", "notes": "Checkout calls the payment API."}).json()
    assert saved["context"]["source"] == "run" and saved["context"]["notes"] == "Checkout calls the payment API."
    assert saved["highlights"]["intro"][1].endswith("20 concurrent users from the us-east-1 region.")

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
    assert isinstance(detail["baseline"]["improvements"], list)
    assert detail["context"]["source"] == "inherited" and detail["context"]["from_run"] == job["run_id"]
    assert "us-east-1" in detail["highlights"]["intro"][1]
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


def test_server_screenshots_in_the_app(client, tmp_path, monkeypatch):
    from projectstack.llm import registry
    from projectstack.llm.base import LLMConfigError
    from tests.test_results_screenshots import FakeVision, png, shot_entry
    path = write_csv(tmp_path / "s.jtl", [sample(START_MS + i * 1000, 100, "GET /a", "T 1-1", 1) for i in range(30)])
    with path.open("rb") as f:
        run_id = _wait(client, client.post("/api/results/analyze", files={"file": ("s.jtl", f)}).json()["id"])["run_id"]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(registry, "ollama_vision_models", lambda settings: [])
    base = f"/api/results/runs/{run_id}/screenshots"

    state = client.get(base).json()
    assert state["items"] == [] and state["analysis"] is None and state["providers"]["choices"] == []
    up = client.post(base, files=[("files", ("cpu.png", png(), "image/png")),
                                  ("files", ("notes.txt", b"not a picture", "text/plain"))]).json()
    assert [i["name"] for i in up["items"]] == ["cpu.png"] and "not a PNG" in up["errors"][0]   # the good one is kept
    assert client.post(base, files=[("files", ("x.txt", b"no", "text/plain"))]).status_code == 400
    sid = up["items"][0]["id"]
    img = client.get(f"{base}/{sid}/image")
    assert img.content == png() and img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert client.patch(f"{base}/{sid}", json={"caption": "CPU – app"}).json()["items"][0]["caption"] == "CPU – app"

    def no_key(task, settings, override=None):
        raise LLMConfigError("No Gemini API key")
    monkeypatch.setattr(registry, "provider_for_task", no_key)
    refused = client.post(f"{base}/analysis", json={})
    assert refused.status_code == 400 and "No Gemini API key" in refused.json()["detail"]

    fake = FakeVision({"cpu.png": shot_entry("CPU, app", time_axis="09:00 to 09:05", test_on_chart="09:00 to 09:01",
                                             observations=["CPU stayed flat."])},
                      summary={"observations": ["The app server had headroom."], "caveats": []})
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)
    job = client.post(f"{base}/analysis", json={"provider": "fake", "time_zone": "Asia/Kolkata"}).json()
    assert job["status"] in ("running", "done")                                   # read in the background
    deadline = time.monotonic() + 20
    while job["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
        job = client.get(f"{base}/analysis/{job['id']}").json()
    assert job["status"] == "done", job
    read = job["result"]
    assert read["content"]["observations"] == ["The app server had headroom."] and read["stale"] is False
    assert job["done"] == job["total"] == 1
    assert '"local_time_zone":"Asia/Kolkata (UTC+05:30)"' in fake.batches()[0]["prompt"]
    assert client.get(f"{base}/analysis/nope").status_code == 404
    assert client.get(base).json()["analysis"]["content"]["screenshots"][0]["title"] == "CPU, app"
    report = client.get(f"/api/results/runs/{run_id}/report?audience=tech").text
    assert "Server metrics from screenshots" in report and "The app server had headroom." in report
    assert 'src="data:image/png;base64,' in report

    # Downloads: one self-contained file each, named after the scenario
    for path, name in ((f"report?audience=tech&format=html&download=true", "technical-report.html"),
                       (f"report?audience=exec&format=html&download=true", "executive-summary.html"),
                       (f"report?audience=tech&format=md&download=true", "technical-report.md"),
                       (f"labels-report?format=html&download=true", "requests-and-transactions.html")):
        got = client.get(f"/api/results/runs/{run_id}/{path}")
        assert got.status_code == 200, (path, got.text[:200])
        assert f'filename="{run_id}_{name}"' in got.headers["content-disposition"] or \
            f'_{run_id}_{name}"' in got.headers["content-disposition"], got.headers["content-disposition"]
        if name.endswith(".html"):
            assert got.text.lstrip().lower().startswith("<!doctype html>")
            assert not re.search(r'(src|href)="https?://', got.text)                 # nothing loaded from elsewhere

    assert client.delete(f"{base}/{sid}").json()["items"] == []
    assert client.get(f"{base}/{sid}/image").status_code == 404
    assert client.delete(f"{base}/{sid}").status_code == 404


def test_word_report_and_company_template(client, tmp_path):
    import io
    docx = pytest.importorskip("docx")
    path = write_csv(tmp_path / "w.jtl", [sample(START_MS + i * 1000, 100, "GET /a", "T 1-1", 1) for i in range(30)])
    with path.open("rb") as f:
        run_id = _wait(client, client.post("/api/results/analyze", files={"file": ("w.jtl", f)},
                                           data={"scenario": "Word demo"}).json()["id"])["run_id"]
    state = client.get("/api/results/report-template").json()
    assert state["template"] is None and set(state["doc"]) == {"available", "path", "install"}
    from projectstack.results import office
    real_find = office.find_soffice
    office.find_soffice = lambda settings=None: None                  # no LibreOffice: .doc says why, .docx works
    try:
        no_doc = client.get(f"/api/results/runs/{run_id}/report?format=doc&download=true")
        assert no_doc.status_code == 503 and "libreoffice-writer-nogui" in no_doc.json()["detail"]
        assert client.get("/api/results/report-template").json()["doc"]["available"] is False
    finally:
        office.find_soffice = real_find
    starter = client.get("/api/results/report-template/starter")
    assert starter.status_code == 200 and starter.content[:2] == b"PK"

    built_in = client.get(f"/api/results/runs/{run_id}/report?format=docx&download=true")
    assert built_in.status_code == 200, built_in.text[:300]
    assert built_in.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert f'filename="Word-demo_{run_id}_performance-report.docx"' in built_in.headers["content-disposition"]
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(built_in.content)).paragraphs)
    assert "4. Performance Highlights & Observations" in text and "Upload your company's Word template" in text

    up = client.post("/api/results/report-template", files={"file": ("ACME.docx", starter.content)}).json()
    assert up["template"]["name"] == "ACME.docx" and up["template"]["marker"] is True
    branded = client.get(f"/api/results/runs/{run_id}/report?format=docx&download=true")
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(branded.content)).paragraphs)
    assert "[[REPORT]]" not in text and "{{scenario}}" not in text and "Word demo" in text
    assert "Upload your company's Word template" not in text                          # the template's cover instead
    bad = client.post("/api/results/report-template", files={"file": ("x.docx", b"not a zip")})
    assert bad.status_code == 400 and "not a Word document" in bad.json()["detail"]
    assert client.delete("/api/results/report-template").json()["template"] is None


def test_create_report_from_a_blueprint(client, ch_db, tmp_path):
    import io
    docx = pytest.importorskip("docx")
    from projectstack.config import load_settings
    from projectstack.results import blueprint as bp
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    from projectstack.results.report import locate
    from tests.test_results_blueprint import make_blueprint
    rows = sorted([sample(START_MS + i * 500, 100 + i % 7, "GET /search", "US - Search 1-2", 2) for i in range(400)]
                  + [sample(START_MS + 250 + i * 500, 80, "POST /checkout", "US - Checkout 1-1", 1) for i in range(400)],
                  key=lambda r: r["timeStamp"])
    path = write_csv(tmp_path / "bp.jtl", rows)
    with path.open("rb") as f:
        run_id = _wait(client, client.post("/api/results/analyze", files={"file": ("bp.jtl", f)},
                                           data={"scenario": "Blueprint demo"}).json()["id"])["run_id"]
    settings = load_settings()
    db = ResultsDB(ClickHouseConfig.from_settings({**settings, "clickhouse_database": ch_db}))
    folder, doc = locate(run_id, Path(settings["runs_dir"]))
    assert bp.thread_groups(db, run_id) == {"Search": 2, "Checkout": 1}
    data, _ = bp.build(make_blueprint(), [bp.RunInput(folder, doc, "Load Test - US region", "US")],
                       settings=settings, db=db)
    d = docx.Document(io.BytesIO(data))
    assert len(d.inline_shapes) == 4 + 0                         # the four graphs redrawn; no screenshots attached
    agg = d.tables[-1]
    assert agg.rows[0].cells[0].text == "Label" and agg.rows[0].cells[12].text == "KB/s out"
    assert {r.cells[0].text for r in agg.rows[1:]} == {"GET /search", "POST /checkout"}
    assert agg.rows[1].cells[1].text == "400"
    workload = d.tables[1]
    assert [c.text for c in workload.rows[2].cells] == ["J01", "Search", "50%", "2"]   # users per journey from the run
    assert workload.rows[1].cells[3].text == "3"


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
