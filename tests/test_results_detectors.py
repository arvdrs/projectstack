"""
Results analysis, phase 2: steady-state detection, detectors and findings.json.
Synthetic runs plant one problem each at a known minute; the detectors must find
it there with the right values, and a clean run must produce no findings.
"""

import json
import secrets

import numpy as np
import pytest

from projectstack.config import load_settings
from projectstack.results.analyze import analyze_file
from projectstack.results.db import ClickHouseConfig, ResultsDB
from projectstack.results.detectors import Finding
from projectstack.results.detectors.changepoint import change_points
from projectstack.results.findings import BUDGET_BYTES, build, verdict
from projectstack.results.steady import detect

from tests.jtl_synth import SCENARIO_START_MS, bulk_csv, ramp_steady, scenario_csv, stepping

MIN = 60_000


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    db_name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": db_name, "runs_dir": str(tmp_path_factory.mktemp("runs"))}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    yield s
    db.command(f"DROP DATABASE IF EXISTS {db_name}")


@pytest.fixture(scope="module")
def run(settings, tmp_path_factory):
    folder = tmp_path_factory.mktemp("jtl")
    cache = {}

    def _run(name, sla=None, steady=None, **scenario):
        key = (name, sla, steady)
        if key not in cache:
            path = folder / f"{name}.jtl"
            if not path.exists():
                scenario_csv(path, **scenario)
            cache[key] = analyze_file(path, settings=settings, scenario=name, sla=sla, steady=steady)
        return cache[key]
    return _run


def _types(result):
    return [f["type"] for f in result.findings["findings"]]


def _one(result, kind):
    found = [f for f in result.findings["findings"] if f["type"] == kind]
    assert len(found) == 1, result.findings["findings"]
    return found[0]


# ---------------------------------------------------------------------------
# Planted problems
# ---------------------------------------------------------------------------

def test_clean_run_has_no_findings(run):
    r = run("clean", sla="p95=3000,err=1", users_fn=ramp_steady(), with_tc=True)
    assert r.findings["findings"] == []
    assert r.findings["verdict"] == "pass"
    assert [s["label"] for s in r.metrics["labels"]["transaction"]] == ["TC Checkout"]
    # 50 users from ~t+283s (ramp) to ~t+2407s (ramp-down), trimmed 60 s each side
    steady = r.findings["steady"]
    assert steady["method"] == "allThreads" and steady["plateau_users"] == 50
    assert abs(steady["start_offset_s"] - 343) <= 3 and abs(steady["end_offset_s"] - 2347) <= 3


def test_latency_knee_found_at_the_planted_minute(run):
    r = run("knee", users_fn=ramp_steady(), latency_ms=lambda s, u: 200.0 if s < 18 * 60 else 600.0)
    f = _one(r, "latency_change")
    assert f["start_ms"] == SCENARIO_START_MS + 18 * MIN
    # log-normal(σ=0.3) p95 = median · e^(1.645·0.3) ≈ 1.638 × median
    assert f["evidence"]["before"] == pytest.approx(327, rel=0.05)
    assert f["evidence"]["after"] == pytest.approx(983, rel=0.05)
    assert f["severity"] == "critical" and f["evidence"]["users_after"] == 50
    assert len(f["labels"]) >= 3
    assert _types(r) == ["latency_change"]


def test_error_burst_window_and_cause(run):
    r = run("burst", users_fn=ramp_steady(), error_code=("503", "Service Unavailable"),
            error_rate=lambda s: 0.3 if 12 * 60 <= s < 14 * 60 else 0.005)
    f = _one(r, "error_burst")
    assert (f["start_ms"], f["end_ms"]) == (SCENARIO_START_MS + 12 * MIN, SCENARIO_START_MS + 14 * MIN)
    assert f["evidence"]["causes"][0]["code"] == "503"
    assert f["evidence"]["causes"][0]["message"] == "Service Unavailable"
    assert 28 <= f["evidence"]["peak_error_pct"] <= 33
    assert _types(r) == ["error_burst"]          # a recovered burst is not also an error-rate change


def test_saturation_knee_users(run):
    r = run("saturation", users_fn=stepping(), capacity_users=60,
            latency_ms=lambda s, u: 200.0 * max(1.0, u / 60))
    f = _one(r, "saturation")
    assert f["evidence"]["knee_users"] == 60
    assert f["evidence"]["max_tps"] == pytest.approx(120, rel=0.05)
    # Peak load held for only 3 of 30 minutes: no steady state, said so
    assert r.findings["steady"]["method"] == "full-run fallback"
    assert any(s["detector"] == "injector_throughput" for s in r.findings["skipped"])


def test_injector_problems(run):
    r = run("injector", users_fn=ramp_steady(), connect_ms=lambda s: 5.0 if s < 20 * 60 else 80.0,
            tps_factor=lambda s: 0.6 if 25 * 60 <= s < 28 * 60 else 1.0)
    connect = _one(r, "injector_connect")
    assert connect["start_ms"] == SCENARIO_START_MS + 20 * MIN
    assert connect["evidence"]["connect_after_ms"] == pytest.approx(80, abs=3)
    tps = _one(r, "injector_throughput")
    assert (tps["start_ms"], tps["end_ms"]) == (SCENARIO_START_MS + 25 * MIN, SCENARIO_START_MS + 28 * MIN)
    assert tps["evidence"]["tps_during"] == pytest.approx(0.6 * tps["evidence"]["tps_typical"], rel=0.05)
    assert "latency_change" not in _types(r)


def test_sla_is_judged_on_the_steady_state_only(run):
    slow_ramp = dict(users_fn=ramp_steady(), latency_ms=lambda s, u: 2500.0 if s < 300 else 200.0)
    steady = run("slow_ramp", sla="p95=1000", **slow_ramp)
    assert steady.findings["verdict"] == "pass"                 # ramp-up excluded
    full = run("slow_ramp", sla="p95=1000", steady="0:", **slow_ramp)
    assert full.findings["verdict"] == "fail"                    # the same run, ramp-up included
    assert full.findings["findings"][0]["type"] == "sla_breach"


def test_findings_json_written_and_cli_shows_findings(run, settings, monkeypatch):
    from click.testing import CliRunner
    from projectstack.cli.main import cli
    r = run("burst", users_fn=ramp_steady(), error_code=("503", "Service Unavailable"),
            error_rate=lambda s: 0.3 if 12 * 60 <= s < 14 * 60 else 0.005)
    doc = json.loads((r.folder / "findings.json").read_text())
    assert doc["schema"] == "projectstack.findings/1" and doc["findings"][0]["type"] == "error_burst"
    assert doc["size_bytes"] <= BUDGET_BYTES
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", settings["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", settings["runs_dir"])
    out = CliRunner().invoke(cli, ["runs", "show", r.run_id]).output
    assert "Error burst at" in out and "Verdict: not judged" in out


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def test_change_points_are_exact_and_quiet_on_noise():
    rng = np.random.default_rng(0)
    step = np.r_[rng.normal(200, 10, 18), rng.normal(600, 30, 22)]
    assert change_points(step) == [18]
    false_alarms = sum(bool(change_points(rng.normal(200, 15, 40))) for _ in range(200))
    assert false_alarms <= 2


def test_steady_detection_and_fallback():
    bounds = {"start_ms": 0, "last_ts_ms": 2_520_000, "end_ms": 2_520_500}
    users = [(s * 1000, min(50, 1 + s // 6) if s < 2400 else max(0, 50 - (s - 2400) // 2)) for s in range(2520)]
    st = detect(users, 1, bounds, "allThreads")
    assert st.window.source == "auto" and st.plateau_users == 50
    # ≥ 48 users (95% of 50) from s=282 to s=2405; 60 s trimmed at each end
    assert (st.window.start_ms, st.window.end_ms) == (342_000, 2_346_000)
    ramp_only = [(s * 1000, 1 + s // 10) for s in range(600)]
    fb = detect(ramp_only, 1, {"start_ms": 0, "last_ts_ms": 599_000, "end_ms": 599_500}, "allThreads")
    assert fb.method == "full-run fallback" and "No steady state" in fb.note
    steps = [(s * 1000, 10 * (1 + s // 120)) for s in range(600)]
    levels = detect(steps, 1, {"start_ms": 0, "last_ts_ms": 599_000, "end_ms": 599_500}, "allThreads").levels
    assert [l["users"] for l in levels] == [10, 20, 30, 40, 50]


def test_findings_stay_under_budget_with_many_labels(settings, tmp_path):
    path = bulk_csv(tmp_path / "many.jtl", rows=150_000, labels=500, minutes=60)
    r = analyze_file(path, settings=settings, scenario="many")
    doc = r.findings
    assert doc["size_bytes"] <= BUDGET_BYTES
    assert doc["truncated"]["labels.request"]["total"] == 500
    # Labels named in a finding are always kept
    big = Finding("latency_change", "warning", "t", "d", labels=["Transaction 499 /api/endpoint/499"])
    kept = build(r.meta, r.metrics, doc["steady"], [big], [], {"defined": False})
    assert any(x["label"] == "Transaction 499 /api/endpoint/499" for x in kept["labels"]["request"])


def test_verdicts():
    burst = Finding("error_burst", "warning", "t", "d")
    breach = Finding("sla_breach", "critical", "t", "d")
    assert verdict({"defined": False}, [burst]) == "no_sla"
    assert verdict({"defined": True, "passed": False}, [breach]) == "fail"
    assert verdict({"defined": True, "passed": True}, [burst]) == "at_risk"
    assert verdict({"defined": True, "passed": True}, []) == "pass"
