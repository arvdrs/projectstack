"""
Real-world result files: problems found in production JTLs that must not stop an analysis.
"""

import secrets

import pytest

from projectstack.results.analyze import analyze_file

from tests.jtl_synth import START_MS, sample, write_csv


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    from projectstack.config import load_settings
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": name, "runs_dir": str(tmp_path_factory.mktemp("runs"))}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    yield s
    db.command(f"DROP DATABASE IF EXISTS {name}")


def test_negative_values_are_skipped_or_clamped(settings, tmp_path):
    """JMeter writes elapsed = -startTime for a Transaction Controller that never completed (threads
    stopped mid-transaction); other columns can be -1. One such row used to reject the whole file."""
    rows = [sample(START_MS + i * 1000, 100 + i, "GET /a", "T 1-1", 2) for i in range(60)]
    tc = sample(START_MS + 30_500, 0, "TC02 - Chat Room", "T 1-1", 2, code="", msg="")
    tc["elapsed"], tc["URL"] = -(START_MS + 30_500), "null"
    odd = sample(START_MS + 31_000, 50, "GET /a", "T 1-1", 2)
    odd["Connect"], odd["Latency"], odd["IdleTime"] = -1, -5, -2
    path = write_csv(tmp_path / "neg.jtl", sorted(rows + [tc, odd], key=lambda r: r["timeStamp"]))
    result = analyze_file(path, settings=settings, steady="0:")
    assert result.ingest.rows == 61
    assert result.findings["warnings"] == [
        "Skipped 1 sample(s) with a negative elapsed time — JMeter writes these for Transaction Controllers that "
        "did not complete (for example when threads are stopped)"]
    assert result.metrics["overall"]["request"]["min_ms"] == 50                      # the clamped row is kept
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    db = ResultsDB(ClickHouseConfig.from_settings(settings))
    assert db.rows(f"SELECT min(connect), min(latency), min(idle_time) FROM {db.db}.samples "
                   "WHERE run_id = {r:String}", {"r": result.run_id}) == [(0, 0, 0)]


def test_thousands_of_labels(settings, tmp_path):
    """Label lists used to be sent as a query parameter, which ClickHouse rejects above its size
    limit (a 6,000-user test with per-host labels had 5,189 of them)."""
    rows = []
    for i in range(6000):
        label = f"AU - SC03 [{i:05d}abcdef.websocket-in.example.io/api/v1/online-members/with/a/long/path] - 014"
        rows.append(sample(START_MS + i * 50, 80 + i % 40, label, f"T 1-{i % 50 + 1}", 50))
        rows.append(sample(START_MS + i * 50 + 20, 90, label, f"T 1-{i % 50 + 1}", 50))
    path = write_csv(tmp_path / "labels.jtl", rows)
    result = analyze_file(path, settings=settings, steady="0:")
    assert result.meta["labels"]["request"] == 6000
    assert result.metrics["overall"]["request"]["samples"] == 12_000
    assert len(result.metrics["labels"]["request"]) == 6000
    assert result.findings["size_bytes"] <= 30_000 and result.findings["truncated"]["labels.request"]["total"] == 6000
