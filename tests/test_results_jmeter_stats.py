"""
The Statistics table of JMeter's HTML report, reproduced from a run's samples
(projectstack.results.jmeter_stats) and served to the web app.

A small file with known contents: every figure is checked against a reference
computed here the way JMeter's dashboard computes it.
"""

import json
import math
import secrets

import pytest

from projectstack.results import jmeter_stats
from projectstack.results.analyze import analyze_file
from projectstack.results.db import ClickHouseConfig, ResultsDB

from tests.jtl_synth import START_MS, TC_MSG, sample, write_csv

EMPTY = "Number of samples in transaction : 0, number of failing samples : 0"


def legacy_percentile(values, p):
    """Commons Math Percentile, default (LEGACY) estimation, as JMeter's dashboard uses it."""
    s = sorted(values)
    pos = p * (len(s) + 1)
    if pos < 1:
        return float(s[0])
    if pos >= len(s):
        return float(s[-1])
    f = math.floor(pos)
    return s[f - 1] + (pos - f) * (s[f] - s[f - 1])


def rows():
    """'Search': 40 requests, every 10th failing; 'Login': a transaction over 'Login submit',
    with 15 empty runs (an If Controller skipped what is inside)."""
    out = []
    for i in range(40):
        ts = START_MS + i * 1000
        ok = i % 10 != 9
        out.append(sample(ts, 100 + (i * 37) % 400, "Search", "TG 1-1", 5, ok=ok,
                          code="200" if ok else "500", msg="OK" if ok else "Server Error"))
    for i in range(20):
        ts = START_MS + 500 + i * 2000
        child = sample(ts, 200 + i * 10, "Login submit", "TG 1-2", 5)
        parent = sample(ts, 200 + i * 10, "Login", "TG 1-2", 5, msg=TC_MSG.format(n=1, f=0))
        parent["dataType"] = ""
        out += [child, parent]
    for i in range(15):
        empty = sample(START_MS + 900 + i * 2000, 0, "Login", "TG 1-2", 5, msg=EMPTY)
        empty["dataType"] = ""
        out.append(empty)
    return sorted(out, key=lambda r: r["timeStamp"] + r["elapsed"])


def reference(samples):
    """One dashboard row from raw samples: whole set for counts/mean, last WINDOW by end time for percentiles."""
    n = len(samples)
    by_end = sorted(samples, key=lambda r: r["timeStamp"] + r["elapsed"])
    last = [r["elapsed"] for r in by_end[-jmeter_stats.WINDOW:]]
    span = (max(r["timeStamp"] + r["elapsed"] for r in samples) - min(r["timeStamp"] for r in samples)) / 1000
    errors = sum(r["success"] == "false" for r in samples)
    return {
        "samples": n, "errors": errors, "error_pct": errors / n * 100,
        "avg_ms": sum(r["elapsed"] for r in samples) / n,
        "min_ms": min(r["elapsed"] for r in samples), "max_ms": max(r["elapsed"] for r in samples),
        "median_ms": legacy_percentile(last, 0.5), "p90_ms": legacy_percentile(last, 0.9),
        "p95_ms": legacy_percentile(last, 0.95), "p99_ms": legacy_percentile(last, 0.99),
        "tps": n / span, "received_kbps": sum(r["bytes"] for r in samples) / 1024 / span,
        "sent_kbps": sum(r["sentBytes"] for r in samples) / 1024 / span,
    }


def assert_row(got, want):
    for k, v in want.items():
        assert got[k] == pytest.approx(v, abs=0.006), k


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
    data = rows()
    run = analyze_file(write_csv(tmp_path_factory.mktemp("jtl") / "r.jtl", data), settings=s, scenario="dash")
    yield {"db": db, "run": run, "rows": data, "settings": s}
    db.command(f"DROP DATABASE IF EXISTS {name}")


def test_rows_match_jmeter_dashboard(env):
    t = jmeter_stats.compute(env["db"], env["run"].run_id)
    by = {r["label"]: r for r in t["labels"]}
    assert [r["label"] for r in t["labels"]] == ["Login", "Login submit", "Search"]
    data = env["rows"]
    for label in ("Search", "Login submit"):
        assert_row(by[label], reference([r for r in data if r["label"] == label]))
    # Empty transactions are left out: 20 Login samples, not 35, and no 0 ms among them
    login = [r for r in data if r["label"] == "Login" and r["responseMessage"] != EMPTY]
    assert_row(by["Login"], reference(login))
    assert by["Login"]["min_ms"] == 200 and by["Login"]["kind"] == "transaction"
    # Total: requests only (Transaction Controller samples are not counted twice)
    assert_row(t["total"], reference([r for r in data if not r["responseMessage"].startswith("Number of samples")]))
    assert t["total"]["samples"] == 60 and t["total"]["errors"] == 4


def test_percentiles_cover_the_last_window(env, monkeypatch):
    search_rows = [r for r in env["rows"] if r["label"] == "Search"]
    all_elapsed = [r["elapsed"] for r in search_rows]
    monkeypatch.setattr(jmeter_stats, "WINDOW", 7)
    t = jmeter_stats.compute(env["db"], env["run"].run_id)
    search = next(r for r in t["labels"] if r["label"] == "Search")
    want = reference(search_rows)                                # WINDOW patched: percentiles of the last 7 to end
    assert search["p95_ms"] == pytest.approx(want["p95_ms"], abs=0.006)
    assert search["median_ms"] == pytest.approx(want["median_ms"], abs=0.006)
    assert search["p95_ms"] != pytest.approx(legacy_percentile(all_elapsed, 0.95), abs=1)   # not over all 40
    # Counts, mean and extremes still cover every sample
    assert search["samples"] == 40 and search["avg_ms"] == pytest.approx(want["avg_ms"], abs=0.006)
    assert search["max_ms"] == max(all_elapsed)


def test_load_keeps_the_table_with_the_run(env, tmp_path):
    first = jmeter_stats.load(env["db"], env["run"].run_id, tmp_path)
    assert (tmp_path / jmeter_stats.CACHE_FILE).exists()
    (tmp_path / jmeter_stats.CACHE_FILE).write_text((tmp_path / jmeter_stats.CACHE_FILE).read_text()
                                                    .replace('"samples": 60', '"samples": 61'))
    assert jmeter_stats.load(env["db"], env["run"].run_id, tmp_path)["total"]["samples"] == 61    # read back, not recomputed
    assert first["total"]["samples"] == 60
    with pytest.raises(LookupError):
        jmeter_stats.compute(env["db"], "no-such-run")


def test_highlights_totals_are_the_html_report_total(env, tmp_path):
    from projectstack.results import highlights
    doc = env["run"].findings
    assert doc["jmeter_total"] == jmeter_stats.compute(env["db"], env["run"].run_id)["total"]
    assert (env["run"].folder / jmeter_stats.CACHE_FILE).exists()                 # written by the analysis
    t = highlights.totals(doc)
    assert (t["requests"], t["errors"], t["error_pct"]) == (60, 4, round(4 / 60 * 100, 3))
    assert t["throughput_per_s"] == round(doc["jmeter_total"]["tps"], 2)
    stats = {s["name"]: s["value"] for s in highlights.rules(doc)["stats"]}
    assert stats["Total requests"] == "60" and stats["Total errors"] == "4 (6.67%)"
    assert highlights.rules(doc)["observations"][0].startswith("Executed 60 total requests with 4 errors (6.67%")

    # A run analysed before the row was kept: added once from the samples, saved in findings.json
    old = {k: v for k, v in doc.items() if k != "jmeter_total"}
    (tmp_path / "findings.json").write_text(json.dumps(old))
    new = jmeter_stats.ensure_total(old, tmp_path, lambda: env["db"])
    assert new["jmeter_total"] == doc["jmeter_total"] and "jmeter_total" not in old
    assert json.loads((tmp_path / "findings.json").read_text())["jmeter_total"]["samples"] == 60

    def down():
        raise RuntimeError("database down")
    assert "jmeter_total" not in jmeter_stats.ensure_total(old, tmp_path / "elsewhere", down)   # unchanged


def test_same_plain_sentence_is_said_once(env, monkeypatch):
    from projectstack.results import highlights
    doc = {**env["run"].findings, "findings": [
        {"type": "correlation", "severity": "critical", "title": "redirected to login"},
        {"type": "correlation", "severity": "critical", "title": "401 after succeeding"},
        {"type": "error_burst", "severity": "warning", "title": "burst"}]}
    monkeypatch.setattr(highlights, "_plain", lambda x: "Burst." if x["type"] == "error_burst" else "Session lost.")
    obs = highlights.observations(doc, highlights.facts(doc))
    assert obs.count("Session lost.") == 1 and "Burst." in obs


def test_endpoint(env, tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from projectstack.api import main as api
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", env["settings"]["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", env["settings"]["runs_dir"])
    client = TestClient(api.app)
    r = client.get(f"/api/results/runs/{env['run'].run_id}/jmeter-statistics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == env["run"].run_id and body["window"] == 20000 and body["percentiles"] == [0.9, 0.95, 0.99]
    assert body["total"]["samples"] == 60 and {x["label"] for x in body["labels"]} == {"Login", "Login submit", "Search"}
    assert client.get("/api/results/runs/no-such-run/jmeter-statistics").status_code == 404
