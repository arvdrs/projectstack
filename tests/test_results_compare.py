"""
Results analysis, phase 4: run comparison, the regression detector, last-green
baselines and CI mode (exit codes, summary.md, artifacts).
"""

import json
import secrets

import pytest
from click.testing import CliRunner

from projectstack.results import ci, numbers
from projectstack.results.compare import (
    CompareError, RegressionThresholds, RunData, compare, load_run, regression_findings,
)
from projectstack.results.narrative import from_rules
from projectstack.results.report import render
from projectstack.results.summary import render_comparison

from tests.jtl_synth import ramp_steady, scenario_csv


def _label(label, samples=5000, p95=300, err=0.5, tps=10.0):
    return {"label": label, "samples": samples, "avg_ms": p95 * 0.6, "p90_ms": p95 * 0.9, "p95_ms": p95,
            "p99_ms": p95 * 1.3, "max_ms": p95 * 3, "errors": int(samples * err / 100), "error_pct": err, "tps": tps}


def _run(run_id, users=40, labels=None, tx=None, tag=""):
    labels = labels or [_label("Login"), _label("Search")]
    tx = tx if tx is not None else [_label("TC Journey", p95=600)]
    req_overall = _label("all", samples=sum(r["samples"] for r in labels), p95=max(r["p95_ms"] for r in labels),
                         err=max(r["error_pct"] for r in labels), tps=sum(r["tps"] for r in labels))
    metrics = {"window": {"start_ms": 0, "end_ms": 1}, "labels": {"request": labels, "transaction": tx},
               "overall": {"request": req_overall, **({"transaction": dict(tx[0])} if tx else {})},
               "timeline": {"points": []}}
    meta = {"run_id": run_id, "scenario": "search", "tag": tag, "created_at": "2026-10-13T10:00:00+00:00",
            "verdict": "pass", "steady": {"plateau_users": users}}
    return RunData(run_id, meta, metrics)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def test_thresholds_from_settings_and_spec():
    th = RegressionThresholds.from_settings({"results_regression": json.dumps({"p95_pct": 20, "bogus": 1})},
                                            "p95ms=100,err=2,samples=50,load=15%")
    assert (th.p95_pct, th.p95_ms, th.err_pp, th.min_samples, th.load_pct) == (20, 100, 2, 50, 15)
    assert RegressionThresholds().with_spec("p95=15%").p95_pct == 15
    with pytest.raises(ValueError, match="cannot read"):
        RegressionThresholds().with_spec("p99=10")
    with pytest.raises(ValueError, match="needs a number"):
        RegressionThresholds().with_spec("p95=lots")


# ---------------------------------------------------------------------------
# Comparison rules
# ---------------------------------------------------------------------------

def test_p95_and_error_regressions_need_both_thresholds():
    base = _run("a", labels=[_label("Login", p95=300), _label("Search", p95=300), _label("Cart", p95=300, err=0.5),
                             _label("Rare", samples=50, p95=300), _label("Gone")])
    cur = _run("b", labels=[_label("Login", p95=480),            # +60%, +180 ms → critical p95 regression
                            _label("Search", p95=330),           # +10%, +30 ms → under the 50 ms floor
                            _label("Cart", p95=290, err=2.0),    # +1.5 pt → error regression (warning)
                            _label("Rare", samples=50, p95=900),  # too few samples to judge
                            _label("New")])
    comp = compare(base, cur)
    rows = {r["label"]: r for r in comp["kinds"]["request"]["labels"]}
    assert rows["Login"]["regressed"] == ["p95"] and rows["Login"]["severity"] == "critical"
    assert rows["Search"]["status"] == "same"
    assert rows["Cart"]["regressed"] == ["error_rate"] and rows["Cart"]["severity"] == "warning"
    assert rows["Rare"]["status"] == "too_few_samples"
    assert comp["only_in_baseline"]["request"] == ["Gone"] and comp["only_in_current"]["request"] == ["New"]
    assert [r["label"] for r in comp["regressions"]] == ["Login", "Cart"]
    assert comp["load"]["comparable"] and comp["gating"]
    assert comp["summary"] == {"compared": 4, "regressed": 2, "improved": 0, "too_few_samples": 1}


def test_improvements_and_unchanged_runs_do_not_gate():
    base = _run("a", labels=[_label("Login", p95=500, err=3.0), _label("Search", p95=300)])
    cur = _run("b", labels=[_label("Login", p95=300, err=1.0), _label("Search", p95=310)])
    comp = compare(base, cur)
    rows = {r["label"]: r for r in comp["kinds"]["request"]["labels"]}
    assert rows["Login"]["improved"] == ["p95", "error_rate"] and rows["Search"]["status"] == "same"
    assert not comp["regressions"] and not comp["gating"]


def test_different_load_is_not_comparable():
    base = _run("a", users=40)
    cur = _run("b", users=60, labels=[_label("Login", p95=600), _label("Search")])
    comp = compare(base, cur)
    assert not comp["load"]["comparable"] and "differ by 50%" in comp["load"]["reason"]
    assert comp["regressions"] and not comp["gating"]
    findings = regression_findings(comp, {"start_ms": 1, "end_ms": 2})
    assert [f.severity for f in findings] == ["info"] and "Not counted" in findings[0].detail
    # Without user counts, comparability falls back to throughput
    base.meta["steady"], cur.meta["steady"] = {}, {}
    assert compare(base, cur)["load"]["comparable"]


def test_regression_findings_list_worst_labels():
    base = _run("a", tag="build-1", labels=[_label(f"L{i}", p95=200) for i in range(15)])
    cur = _run("b", labels=[_label(f"L{i}", p95=200 + 20 * i) for i in range(15)])
    comp = compare(base, cur)
    [f] = regression_findings(comp, {"start_ms": 1, "end_ms": 2}, limit=10)
    assert f.type == "regression" and f.evidence["count"] == 12      # L3 (+60 ms, +30%) onwards
    assert len(f.evidence["labels"]) == 10 and f.evidence["labels"][0]["label"] == "L14"
    assert f.title.startswith("p95 regressed for 12 requests vs build-1: worst L14 200 → 480 ms (+140%)")
    assert f.severity == "critical"


# ---------------------------------------------------------------------------
# CI exit codes and the merge-request summary
# ---------------------------------------------------------------------------

def _doc(sla_passed=True, defined=True, verdict="pass"):
    return {"run": {"run_id": "r1", "scenario": "search", "tag": "build-2", "peak_users": 40, "duration_s": 1440},
            "steady": {"duration_s": 1100}, "verdict": verdict,
            "sla": ({"defined": True, "passed": sla_passed,
                     "checks": [{"kind": "response_time", "label": "TC | Journey", "metric": "p95", "unit": "ms",
                                 "target": 500.0, "actual": 873.0 if not sla_passed else 400.0,
                                 "passed": sla_passed},
                                {"kind": "error_rate", "label": None, "metric": "error_rate", "unit": "%",
                                 "target": 1.0, "actual": 0.49, "passed": True}]}
                    if defined else {"defined": False}),
            "findings": []}


@pytest.mark.parametrize("sla_passed, defined, gating, comparable, expected", [
    (True, True, False, True, 0),
    (False, True, False, True, 1),
    (True, True, True, True, 2),
    (False, True, True, True, 3),
    (True, False, False, True, 0),     # no SLA, nothing regressed
    (True, False, False, False, 0),    # regressions at different load do not gate
])
def test_exit_codes(sla_passed, defined, gating, comparable, expected):
    comparison = {"gating": gating, "load": {"comparable": comparable}}
    assert ci.exit_code(_doc(sla_passed, defined), comparison) == expected
    assert ci.exit_code(_doc(sla_passed, defined), None) == (0 if sla_passed or not defined else 1)


def test_summary_markdown(tmp_path, monkeypatch):
    base = _run("base-run", tag="build-1")
    cur = _run("r1", labels=[_label("Login", p95=480), _label("Search")])
    comp = compare(base, cur)
    doc = _doc(sla_passed=False, verdict="fail")
    doc["baseline"] = {"found": True, "run_id": "base-run", "tag": "build-1", "load": comp["load"],
                       "summary": comp["summary"]}
    doc["findings"] = [{"type": "error_burst", "severity": "critical", "title": "Error burst at t+12m"}]
    report = tmp_path / "ws" / "perf" / "report.html"
    monkeypatch.setenv("CI_PROJECT_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("CI_JOB_URL", "https://gitlab.example.com/team/app/-/jobs/42")
    md = ci.summary_markdown(doc, comp, ci.exit_code(doc, comp), report)
    assert md.startswith("### ❌ SLA breach and regression · performance test: search")
    assert "Baseline `base-run` (build-1) — load comparable; 1 regressed" in md
    assert "| TC \\| Journey p95 | 500 ms | 873 ms | ❌ |" in md and "| Error rate | 1% | 0.49% | ✅ |" in md
    assert "| Login | 300 → 480 ms ❌ | +60% |" in md
    assert "🔴 critical: Error burst at t+12m" in md
    assert "[Full report](https://gitlab.example.com/team/app/-/jobs/42/artifacts/file/perf/report.html)" in md
    monkeypatch.delenv("CI_JOB_URL")
    assert f"Full report: `{report}`" in ci.summary_markdown(doc, comp, 3, report)


def test_comparison_views_and_report_section():
    base = _run("base-run", tag="build-1")
    cur = _run("r1", labels=[_label("Login | admin", p95=480), _label("Search", p95=300, err=2.5)])
    cur.metrics["labels"]["request"][0]["label"] = "Login | admin"
    base.metrics["labels"]["request"][0]["label"] = "Login | admin"
    comp = compare(base, cur)
    text = render_comparison(comp)
    assert "✖ p95" in text and "✖ error_rate" in text and "2 regressed" in text
    md = ci.comparison_markdown(comp)
    assert "Login \\| admin" in md and "❌ p95" in md

    from tests.test_results_report import sample_doc
    doc = sample_doc()
    doc["verdict"] = "at_risk"
    doc["baseline"] = {"requested": "last-green", "found": True, "run_id": "base-run", "tag": "build-1",
                       "created_at": "2026-10-12T10:00:00+00:00", "load": comp["load"], "gating": True,
                       "summary": comp["summary"], "thresholds": comp["thresholds"]}
    doc["findings"] += [f.as_dict(0) for f in regression_findings(comp, {"start_ms": 340000, "end_ms": 2340000})]
    html = render(doc, from_rules(doc, "tech"), "tech", "html")
    assert "Compared with the baseline" in html and "p95 regressions" in html and "Error-rate regressions" in html
    assert html.count('class="tl-band tl-band-critical"') == 2 * 4        # regression windows are not banded
    for audience in ("tech", "exec"):
        n = from_rules(doc, audience)
        check = numbers.check(n.content, doc)
        assert check.ok, check.unverified
    assert any("baseline run" in p for p in from_rules(doc, "exec").content["key_points"])
    md = render(doc, from_rules(doc, "tech"), "tech", "md")
    assert "## Compared with the baseline" in md and "| Login \\| admin | request | 300 ms | 480 ms | +60% |" in md


# ---------------------------------------------------------------------------
# End to end with ClickHouse
# ---------------------------------------------------------------------------

PROFILE = dict(users=20, ramp_s=120, steady_s=900, down_s=60)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from projectstack.config import load_settings
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    db_name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": db_name, "runs_dir": str(tmp_path_factory.mktemp("runs")),
         "anthropic_api_key": "", "gemini_api_key": "", "groq_api_key": "", "openai_api_key": ""}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    folder = tmp_path_factory.mktemp("jtl")
    files = {
        "base": scenario_csv(folder / "base.jtl", ramp_steady(**PROFILE), with_tc=True, seed=11),
        "slow": scenario_csv(folder / "slow.jtl", ramp_steady(**PROFILE), with_tc=True, seed=12,
                             latency_ms=lambda s, u: 320.0),
        "same": scenario_csv(folder / "same.jtl", ramp_steady(**PROFILE), with_tc=True, seed=13),
        "bigger": scenario_csv(folder / "bigger.jtl", ramp_steady(**{**PROFILE, "users": 40}), with_tc=True,
                               seed=14, latency_ms=lambda s, u: 320.0),
    }
    yield {"settings": s, "db": db, "files": files}
    db.command(f"DROP DATABASE IF EXISTS {db_name}")


def test_last_green_baseline_and_regression(env):
    from projectstack.results.analyze import analyze_file
    s, f = env["settings"], env["files"]
    base = analyze_file(f["base"], settings=s, scenario="search", tag="b1", sla="p95=3000,err=1",
                        baseline="last-green")
    assert base.findings["verdict"] == "pass"
    assert base.findings["baseline"] == {"requested": "last-green", "found": False,
                                         "note": base.findings["baseline"]["note"]}
    assert base.comparison is None

    slow = analyze_file(f["slow"], settings=s, scenario="search", tag="b2", sla="p95=3000,err=1",
                        baseline="last-green")
    assert slow.comparison["baseline"]["run_id"] == base.run_id and slow.comparison["gating"]
    reg = [x for x in slow.findings["findings"] if x["type"] == "regression"]
    assert [x["evidence"]["metric"] for x in reg] == ["p95_ms"]
    assert reg[0]["evidence"]["count"] == 7 and reg[0]["severity"] == "critical"   # 6 requests + the transaction
    assert slow.findings["verdict"] == "at_risk" and slow.findings["baseline"]["gating"]
    assert (slow.folder / "comparison.json").exists()
    assert ci.exit_code(slow.findings, slow.comparison) == ci.EXIT_REGRESSION

    # The regressed run is not green: the next run still compares with the first one
    same = analyze_file(f["same"], settings=s, scenario="search", tag="b3", sla="p95=3000,err=1",
                        baseline="last-green")
    assert same.comparison["baseline"]["run_id"] == base.run_id
    assert not same.comparison["regressions"] and same.findings["verdict"] == "pass"

    # More users than the baseline: differences shown, not gating
    bigger = analyze_file(f["bigger"], settings=s, scenario="search", tag="b4", baseline=base.run_id)
    assert not bigger.comparison["load"]["comparable"] and not bigger.comparison["gating"]
    assert {x["severity"] for x in bigger.findings["findings"] if x["type"] == "regression"} == {"info"}

    with pytest.raises(ValueError, match="--baseline"):
        analyze_file(f["same"], settings=s, scenario="search", baseline="20991231")


def test_baseline_recomputed_from_clickhouse(env):
    """A CI runner without the baseline's folder rebuilds its statistics from the samples."""
    from pathlib import Path
    from projectstack.results import store
    s, db = env["settings"], env["db"]
    runs_dir = Path(s["runs_dir"])
    base_id = store.list_runs(db, scenario="search", limit=50)[-1]["run_id"]
    local = load_run(base_id, runs_dir)
    elsewhere = load_run(base_id, runs_dir / "nowhere", db)
    assert elsewhere.source == "clickhouse"
    assert elsewhere.metrics["labels"] == local.metrics["labels"]
    assert elsewhere.metrics["overall"] == local.metrics["overall"]
    with pytest.raises(CompareError, match="no results database"):
        load_run(base_id, runs_dir / "nowhere")


def test_ci_cli_exit_codes_and_artifacts(env, tmp_path, monkeypatch):
    from projectstack.cli.main import cli
    s, f = env["settings"], env["files"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    def run(*args):
        return runner.invoke(cli, ["analyze", *map(str, args)], catch_exceptions=False)

    art = tmp_path / "artifacts"
    res = run(f["slow"], "--scenario", "search", "--tag", "ci-1", "--sla", "p95=3000,err=1",
              "--baseline", "last-green", "--ci", "--no-llm", "--artifacts", art)
    assert res.exit_code == 2, res.output
    assert "### ❌ Regression against the baseline" in res.stdout
    assert {p.name for p in art.iterdir()} == {"report.html", "summary.md", "findings.json", "comparison.json"}
    assert (art / "summary.md").read_text() in res.stdout
    assert "Compared with the baseline" in (art / "report.html").read_text()

    res = run(f["slow"], "--scenario", "search", "--sla", "p95=400", "--baseline", "last-green", "--ci", "--no-llm")
    assert res.exit_code == 3 and "SLA breach and regression" in res.stdout
    res = run(f["same"], "--scenario", "search", "--sla", "p95=200", "--ci", "--no-llm")
    assert res.exit_code == 1
    res = run(f["same"], "--scenario", "search", "--sla", "p95=3000,err=1", "--ci", "--no-llm")
    assert res.exit_code == 0 and "✅ Passed" in res.stdout

    bad = tmp_path / "bad.jtl"
    bad.write_text("this is not a jtl\n")
    res = run(bad, "--ci", "--artifacts", tmp_path / "failed")
    assert res.exit_code == 4 and "analysis failed" in (tmp_path / "failed" / "summary.md").read_text()
    res = run(f["same"], "--ci", "--sla", "p95=nope")
    assert res.exit_code == 4
    res = runner.invoke(cli, ["analyze", str(f["same"]), "--ci", "--no-such-option"])
    assert res.exit_code == 4                       # usage errors must not look like a regression (2)
    res = runner.invoke(cli, ["analyze", str(f["same"]), "--no-such-option"])
    assert res.exit_code == 2


def test_compare_cli(env, tmp_path, monkeypatch):
    from projectstack.cli.main import cli
    from projectstack.results import store
    s, db = env["settings"], env["db"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    runs = {r["tag"]: r["run_id"] for r in store.list_runs(db, scenario="search", limit=50)}
    runner = CliRunner()
    res = runner.invoke(cli, ["compare", runs["b1"], runs["b2"]])
    assert res.exit_code == 0 and "✖ p95" in res.output and "comparable" in res.output
    res = runner.invoke(cli, ["compare", "last-green", runs["b2"], "--format", "json"])
    data = json.loads(res.output)
    assert data["baseline"]["run_id"] == runs["b1"] and data["summary"]["regressed"] == 7
    out = tmp_path / "cmp.md"
    res = runner.invoke(cli, ["compare", runs["b1"], runs["b3"], "--format", "md", "--out", str(out)])
    assert res.exit_code == 0 and "0 regressed" in out.read_text()
    res = runner.invoke(cli, ["compare", runs["b1"], runs["b2"], "--regression", "p95=100%"])
    assert "0 regressed" in res.output
    res = runner.invoke(cli, ["compare", runs["b1"], runs["b1"]])
    assert res.exit_code == 2 and "same run" in res.output
