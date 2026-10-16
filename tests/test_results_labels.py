"""
Per-label synthesis (requests and transactions) and comparing analysed runs:
the labels module, its report, the web endpoints and `pstack labels`.
"""

import secrets
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from click.testing import CliRunner
from fastapi.testclient import TestClient

from projectstack.api import main as api
from projectstack.api import results_api
from projectstack.results import labels
from projectstack.results.analyze import analyze_file
from projectstack.results.db import ClickHouseConfig, ResultsDB

from tests.jtl_synth import ramp_steady, scenario_csv

PROFILE = dict(users=20, ramp_s=120, steady_s=900, down_s=60)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from projectstack.config import load_settings
    name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": name, "runs_dir": str(tmp_path_factory.mktemp("runs"))}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    folder = tmp_path_factory.mktemp("jtl")
    base = analyze_file(scenario_csv(folder / "b.jtl", ramp_steady(**PROFILE), with_tc=True, seed=31),
                        settings=s, scenario="shop", tag="v1", sla="p95=3000,err=1")
    slow = analyze_file(scenario_csv(folder / "s.jtl", ramp_steady(**PROFILE), with_tc=True, seed=32,
                                     latency_ms=lambda sec, u: 200.0 if sec < 9 * 60 else 650.0,
                                     error_code=("503", "Service Unavailable"),
                                     error_rate=lambda sec: 0.08 if 10 * 60 <= sec < 11 * 60 else 0.005),
                        settings=s, scenario="shop", tag="v2", sla="p95=3000,err=1", baseline="last-green")
    yield {"settings": s, "db": db, "base": base, "slow": slow}
    db.command(f"DROP DATABASE IF EXISTS {name}")


def _detail(env, run, label):
    doc = run.findings
    base_metrics, base_info = labels.load_baseline(doc, Path(env["settings"]["runs_dir"]), env["db"])
    return labels.detail(env["db"], run.run_id, label, doc, base_metrics, base_info)


def test_transaction_detail_and_synthesis(env):
    d = _detail(env, env["slow"], "TC Checkout")
    s = d["steady"]
    assert d["kind"] == "transaction" and s["samples"] > 1000
    assert [x["p"] for x in d["ladder"]] == [0.5, 0.75, 0.9, 0.95, 0.99, 0.999]
    assert [x["ms"] for x in d["ladder"]] == sorted(x["ms"] for x in d["ladder"])
    hist = d["histogram"]
    assert sum(b["samples"] for b in hist["bins"]) + hist["overflow"] == s["samples"]
    assert d["share_pct"] == 100.0                                       # the only transaction
    assert d["breakdown"] is None                                        # no first-byte time for a transaction
    assert all("Number of samples in transaction" not in e["message"] for e in d["errors"])
    assert d["baseline"]["tag"] == "v1" and d["baseline"]["p95_change_pct"] > 50
    text = " ".join(d["synthesis"])
    assert "slowed down during the steady state" in text and "Compared with v1" in text
    assert d["status"] == "critical"                                     # named in a critical finding
    assert d["timeline"]["points"] and d["window"]["end_offset_s"] > d["window"]["start_offset_s"]


def test_request_detail(env):
    run = env["base"]
    req = run.metrics["labels"]["request"][0]["label"]
    d = _detail(env, run, req)
    assert d["kind"] == "request" and d["breakdown"]["server_ms"] > 0
    assert sum(c["share_pct"] for c in d["codes"]) == pytest.approx(100, abs=0.1)
    assert len(d["slowest"]) == 10 and d["slowest"][0]["elapsed_ms"] >= d["slowest"][-1]["elapsed_ms"]
    assert d["status"] in ("healthy", "attention") and d["baseline"] is None
    shares = [_detail(env, run, r["label"])["share_pct"] for r in run.metrics["labels"]["request"]]
    assert sum(shares) == pytest.approx(100, abs=0.5)
    with pytest.raises(LookupError):
        _detail(env, run, "no such label")


def test_labels_report(env):
    run = env["slow"]
    folder = Path(env["settings"]["runs_dir"]) / run.run_id
    html, details = labels.build_report(env["db"], folder)
    assert [d["label"] for d in details][0] == "TC Checkout"           # transactions first
    assert len(details) == 7 and "<svg" in html and "Needs action" in html
    assert "nothing in this report was written by AI" in html
    md, _ = labels.build_report(env["db"], folder, out_format="md")
    assert md.startswith("# shop — requests and transactions") and "| TC Checkout | transaction |" in md
    few, _ = labels.build_report(env["db"], folder, limit=2)
    assert "TC Checkout" in few


@pytest.fixture
def client(env, tmp_path, monkeypatch):
    s = env["settings"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    monkeypatch.setattr(results_api, "UPLOAD_DIR", tmp_path / "uploads")
    return TestClient(api.app)


def test_label_endpoints(client, env):
    run_id = env["slow"].run_id
    d = client.get(f"/api/results/runs/{run_id}/label", params={"name": "TC Checkout"}).json()
    assert d["status"] == "critical" and d["synthesis"]
    assert client.get(f"/api/results/runs/{run_id}/label", params={"name": "nope"}).status_code == 404
    rows = client.get(f"/api/results/runs/{run_id}/labels").json()["labels"]
    assert {r["kind"] for r in rows} == {"transaction", "request"} and len(rows) == 7
    html = client.get(f"/api/results/runs/{run_id}/labels-report")
    assert html.status_code == 200 and "requests and transactions" in html.text
    md = client.get(f"/api/results/runs/{run_id}/labels-report", params={"format": "md", "download": "true"})
    assert "attachment" in md.headers["content-disposition"]


def test_compare_endpoints(client, env):
    base, slow = env["base"].run_id, env["slow"].run_id
    for ref in (base, "previous", "last-green"):
        c = client.get("/api/results/compare", params={"base": ref, "current": slow}).json()
        assert c["baseline"]["run_id"] == base, ref
    assert c["summary"]["regressed"] == 7 and c["load"]["comparable"]
    assert c["runs"]["baseline"]["timeline"]["points"] and c["runs"]["current"]["verdict"]
    new_types = {f["type"] for f in c["findings_diff"]["new"]}
    assert "latency_change" in new_types and not c["findings_diff"]["resolved"]
    md = client.get("/api/results/compare/report", params={"base": base, "current": slow})
    assert md.status_code == 200 and "### Findings" in md.text and "New in this run" in md.text
    bad = client.get("/api/results/compare", params={"base": "previous", "current": base})
    assert bad.status_code == 400 and "No earlier run" in bad.json()["detail"]


def test_findings_diff_matching():
    b = {"findings": [{"type": "latency_change", "title": "p95 rose from 362 ms to 1,066 ms at t+17m59s", "severity": "critical"},
                      {"type": "error_burst", "title": "Error burst at t+29m59s for 2 min", "severity": "critical"}]}
    c = {"findings": [{"type": "latency_change", "title": "p95 rose from 410 ms to 980 ms at t+21m", "severity": "critical"},
                      {"type": "saturation", "title": "Saturation at about 60 users", "severity": "warning"}]}
    d = results_api.findings_diff(b, c)
    assert [f["type"] for f in d["persisting"]] == ["latency_change"]
    assert [f["type"] for f in d["new"]] == ["saturation"] and [f["type"] for f in d["resolved"]] == ["error_burst"]


def test_labels_cli(env, tmp_path, monkeypatch):
    from projectstack.cli.main import cli
    s = env["settings"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    runner = CliRunner()
    res = runner.invoke(cli, ["labels", env["slow"].run_id, "TC Checkout"])
    assert res.exit_code == 0 and "needs action" in res.output and "Compared with v1" in res.output
    res = runner.invoke(cli, ["labels", env["slow"].run_id, "--format", "both", "--out", f"{tmp_path}/r/"])
    assert res.exit_code == 0 and (tmp_path / "r" / "report_labels.html").exists() and (tmp_path / "r" / "report_labels.md").exists()
    assert runner.invoke(cli, ["labels", env["slow"].run_id, "nope"]).exit_code == 2
