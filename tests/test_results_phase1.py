"""
Results analysis, phase 1: ingest (CSV / header-less CSV / XML / gzip), metrics,
run registry and the analyze / runs commands. Needs the local ClickHouse
(deploy/clickhouse/docker-compose.yml); skipped when it is not running.
"""

import json
import random
import secrets
from pathlib import Path

import pytest
from click.testing import CliRunner

from projectstack.config import load_settings, save_settings
from projectstack.results.analyze import analyze_file
from projectstack.results.columns import DEFAULT_HEADER, header_from_properties
from projectstack.results.db import ClickHouseConfig, ResultsDB, ResultsDBError
from projectstack.results.ingest import IngestError, sniff
from projectstack.results.sla_spec import parse_sla
from projectstack.results.window import parse_steady

from tests.jtl_synth import JMETER_COLUMNS, START_MS, flow_samples, sample, write_csv, write_xml


def _jmeter_pct(values, p):
    """JMeter Aggregate Report: value at 0-based position floor(N·p) of the sorted samples."""
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    db_name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": db_name, "runs_dir": str(tmp_path_factory.mktemp("runs"))}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except (ResultsDBError, Exception) as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    yield s
    db.command(f"DROP DATABASE IF EXISTS {db_name}")


def _analyze(settings, path, **kw):
    return analyze_file(path, settings=settings, **kw)


# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------

def test_percentiles_match_brute_force(settings, tmp_path):
    rng = random.Random(42)
    rows, by_label = [], {}
    for i in range(1500):
        label = f"L{rng.randint(0, 4)}"
        elapsed = rng.choice([rng.randint(1, 30), rng.randint(1, 5000)])
        rows.append(sample(START_MS + i * 400, elapsed, label, f"T-{i % 7}", 7))
        by_label.setdefault(label, []).append(elapsed)
    # Full run (not the auto steady state), so the brute-force reference sees the same samples
    result = _analyze(settings, write_csv(tmp_path / "pct.jtl", rows), steady="0:")

    for stat in result.metrics["labels"]["request"]:
        vals = by_label[stat["label"]]
        for key, p in (("p50_ms", .5), ("p90_ms", .9), ("p95_ms", .95), ("p99_ms", .99)):
            assert stat[key] == _jmeter_pct(vals, p), (stat["label"], key)
    # Overall percentiles come from all samples, not from averaging labels
    everything = [e for v in by_label.values() for e in v]
    overall = result.metrics["overall"]["request"]
    assert overall["p95_ms"] == _jmeter_pct(everything, .95)
    assert overall["avg_ms"] == round(sum(everything) / len(everything), 1)
    # Each timeline bucket's p95 is computed from that bucket's samples
    bucket_ms = result.metrics["timeline"]["bucket_s"] * 1000
    for pt in result.metrics["timeline"]["points"]:
        vals = [r["elapsed"] for r in rows if pt["t_ms"] <= r["timeStamp"] < pt["t_ms"] + bucket_ms]
        assert pt["p95_ms"] == _jmeter_pct(vals, .95)
        assert pt["samples"] == len(vals)


# ---------------------------------------------------------------------------
# Transaction Controllers and sub-results
# ---------------------------------------------------------------------------

def test_transaction_parents_and_subresults_are_not_double_counted(settings, tmp_path):
    rows = flow_samples(users=3, iterations=4, error_every=3)
    result = _analyze(settings, write_csv(tmp_path / "flow.jtl", rows))
    labels = {s["label"]: s for s in result.metrics["labels"]["request"]}
    tx = {s["label"]: s for s in result.metrics["labels"]["transaction"]}

    assert set(tx) == {"Login"}
    assert "Search-0" not in labels                    # redirect child of Search
    assert "Step-1" in labels                          # only looks like a sub-result
    assert result.meta["labels"] == {"request": 6, "transaction": 1, "subresult": 1}
    plain = [r for r in rows if r["label"] not in ("Login", "Search-0")]
    assert result.metrics["overall"]["request"]["samples"] == len(plain)
    assert result.metrics["overall"]["request"]["errors"] == 4          # child failures, counted once
    assert tx["Login"]["errors"] == 4 and labels["Login submit"]["error_pct"] == pytest.approx(33.333, abs=.01)


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

def test_custom_columns_quotes_tabs_and_gzip(settings, tmp_path):
    rows = flow_samples(users=2, iterations=2)
    rows[3]["responseMessage"] = 'Bad "gateway", retry\nlater'
    cols = [c for c in JMETER_COLUMNS if c not in ("allThreads", "grpThreads", "Connect", "URL")]
    path = write_csv(tmp_path / "custom.jtl.gz", rows, cols, delimiter="\t", gz=True,
                     extra={"customerId": "C-1", "myVar": "x,y"})
    info = sniff(path)
    assert info.gzipped and info.delimiter == "\t" and "Connect" not in info.present
    result = _analyze(settings, path)
    assert result.ingest.rows == len(rows)
    assert result.meta["columns"]["users_source"] == "threadName"     # no allThreads column
    assert result.meta["run"]["peak_users"] == 2


def test_headerless_csv_uses_properties_or_jmeter_defaults(settings, tmp_path):
    rows = flow_samples(users=2, iterations=2)
    props = tmp_path / "user.properties"
    props.write_text("jmeter.save.saveservice.url=false\njmeter.save.saveservice.thread_counts=false\n"
                     "jmeter.save.saveservice.default_delimiter=;\nsample_variables=orderId\n")
    cols, delim = header_from_properties({k: v for k, v in (l.split("=", 1) for l in props.read_text().split("\n") if l)})
    assert delim == ";" and "URL" not in cols and cols[-1] == "orderId"
    path = write_csv(tmp_path / "noheader.jtl", rows, [c for c in cols if c != "orderId"], header=False,
                     delimiter=";", extra={"orderId": "991"})
    assert _analyze(settings, path, props=props).ingest.rows == len(rows)

    default = write_csv(tmp_path / "default.jtl", rows, DEFAULT_HEADER, header=False)
    assert sniff(default).column_source == "jmeter-defaults"
    assert _analyze(settings, default).ingest.rows == len(rows)


def test_xml_jtl(settings, tmp_path):
    rows = flow_samples(users=2, iterations=3, error_every=2)
    # XML nests: the Transaction Controller as a parent sample around its children,
    # and Search's redirect inside Search
    by_key = {(r["threadName"], r["label"], r["timeStamp"]): r for r in rows}
    top = []
    for r in rows:
        if r["label"] in ("Login page", "Login submit", "Search-0"):
            continue
        if r["label"] == "Login":
            r["children"] = [c for c in rows if c["threadName"] == r["threadName"]
                             and c["label"] in ("Login page", "Login submit")
                             and r["timeStamp"] <= c["timeStamp"] <= r["timeStamp"] + r["elapsed"]]
        top.append(r)
    path = write_xml(tmp_path / "results.xml", top)
    result = _analyze(settings, path)
    assert result.ingest.rows == len(by_key)
    labels = {s["label"]: s for s in result.metrics["labels"]["request"]}
    assert "Login submit" in labels and "Search-0" not in labels
    assert [s["label"] for s in result.metrics["labels"]["transaction"]] == ["Login"]
    assert any("code expected to equal" in e["failure"] for e in result.metrics["errors"])
    assert "Connect" in result.meta["columns"]["present"]


def test_timestamps_must_be_epoch_ms(tmp_path):
    rows = flow_samples(users=1, iterations=1)
    for r in rows:
        r["timeStamp"] = r["timeStamp"] // 1000
    with pytest.raises(IngestError, match="seconds"):
        sniff(write_csv(tmp_path / "secs.jtl", rows))
    for r in rows:
        r["timeStamp"] = "2025/10/09 10:00:00.000"
    with pytest.raises(IngestError, match="epoch milliseconds"):
        sniff(write_csv(tmp_path / "fmt.jtl", rows))
    with pytest.raises(IngestError, match="label"):
        sniff(write_csv(tmp_path / "nolabel.jtl", flow_samples(1, 1), ["timeStamp", "elapsed", "success"]))


# ---------------------------------------------------------------------------
# Windows, SLA spec, settings
# ---------------------------------------------------------------------------

def test_steady_window_limits_stats(settings, tmp_path):
    rows = [sample(START_MS + i * 1000, 100 if 60 <= i < 120 else 900, "Page", "T-1", 1) for i in range(180)]
    result = _analyze(settings, write_csv(tmp_path / "steady.jtl", rows), steady="1m:2m")
    stat = result.metrics["overall"]["request"]
    assert stat["samples"] == 60 and stat["max_ms"] == 100
    assert stat["tps"] == pytest.approx(1.0)
    assert result.meta["window"]["source"] == "manual"


def test_window_and_sla_parsing():
    w = parse_steady("5m:1h", START_MS, START_MS + 7_200_000)
    assert (w.start_ms - START_MS, w.end_ms - START_MS) == (300_000, 3_600_000)
    assert parse_steady("90:", START_MS, START_MS + 600_000).start_ms == START_MS + 90_000
    with pytest.raises(ValueError):
        parse_steady("10m:5m", START_MS, START_MS + 3_600_000)
    sla = parse_sla("p95=3000, err=1, p95[Login]=2000, tps>=50")
    assert sla == {"response_time_metric": "p95", "response_time_ms": 3000.0,
                   "transaction_targets": {"Login": 2000.0}, "error_rate_pct": 1.0, "throughput_rps": 50.0}
    with pytest.raises(ValueError, match="one response-time metric"):
        parse_sla("p95=3000,p90[Login]=1000")
    with pytest.raises(ValueError):
        parse_sla("p95=fast")


def test_settings_keep_keys_other_versions_wrote(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "s.json"))
    (tmp_path / "s.json").write_text(json.dumps({"future_key": "keep me", "ai_provider": "groq"}))
    save_settings({"ai_provider": "gemini"})
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved == {"future_key": "keep me", "ai_provider": "gemini"}
    monkeypatch.setenv("PSTACK_CLICKHOUSE_HOST", "ch.internal")
    assert load_settings()["clickhouse_host"] == "ch.internal"
    assert load_settings(apply_env=False)["clickhouse_host"] == "localhost"


def test_clickhouse_url():
    cfg = ClickHouseConfig.from_url("clickhouse://perf:s%40cret@ch.corp:8443/perf_results?secure=1")
    assert (cfg.host, cfg.port, cfg.user, cfg.password, cfg.database, cfg.secure) == \
        ("ch.corp", 8443, "perf", "s@cret", "perf_results", True)


# ---------------------------------------------------------------------------
# Registry + CLI
# ---------------------------------------------------------------------------

def test_cli_analyze_list_show_delete(settings, tmp_path, monkeypatch):
    from projectstack.cli.main import cli
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", settings["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", settings["runs_dir"])
    path = write_csv(tmp_path / "cli.jtl", flow_samples(users=2, iterations=2))
    runner = CliRunner()

    res = runner.invoke(cli, ["analyze", str(path), "--tag", "Build 42", "--sla", "p95=3000"])
    assert res.exit_code == 0, res.output
    assert "Requests (ms)" in res.output and "All transactions" in res.output
    run_id = next(line.split()[1] for line in res.output.splitlines() if line.startswith("Run "))
    assert run_id.endswith("build-42")
    folder = Path(settings["runs_dir"]) / run_id
    assert (folder / "meta.json").exists() and (folder / "metrics.json").exists()

    listed = runner.invoke(cli, ["runs", "list"])
    assert run_id in listed.output
    shown = runner.invoke(cli, ["runs", "show", run_id[:-3]])
    assert shown.exit_code == 0 and "Login submit" in shown.output
    assert runner.invoke(cli, ["runs", "show", "nope"]).exit_code == 2

    bad = runner.invoke(cli, ["analyze", str(path), "--sla", "p95=quick"])
    assert bad.exit_code == 2 and "--sla" in bad.output

    assert runner.invoke(cli, ["runs", "delete", run_id, "--yes"]).exit_code == 0
    assert run_id not in runner.invoke(cli, ["runs", "list"]).output
    assert not folder.exists()
