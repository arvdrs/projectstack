"""
ProjectStack CLI entry point.

Usage:
    pstack parse <har_file>              # Parse and summarize a HAR file
    pstack correlate <har_file>          # Run correlation engine with confidence scores
    pstack generate <har_file>           # Generate JMX from HAR
    pstack validate <jmx_file>           # Validate a JMX file structure
    pstack edit <jmx_file>               # Open JMX in $EDITOR
    pstack run <jmx_file>                # Run a JMX test and show metrics
    pstack export postman <har_file>     # Export HAR as Postman collection
    pstack export grafana <jtl_file>     # Export JTL results to Grafana/InfluxDB
    pstack analyze <jtl_file>            # Load a JTL into ClickHouse and summarise it
    pstack runs list | show | delete     # Analysed runs
    pstack db status                     # Results database connection and contents
"""

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import click

from projectstack.parser.har_parser import parse_har, summarize
from projectstack.correlator.engine import run_correlation
from projectstack.correlator.confidence import annotate as confidence_annotate
from projectstack.generator.jmx_generator import generate_jmx
from projectstack.generator.transaction_grouper import group_transactions
from projectstack.generator.validator import validate_and_report
from projectstack.runner.jmeter_runner import run_jmeter, find_jmeter
from projectstack.runner.metrics_parser import parse_jtl, print_summary
from projectstack.exporters.postman_exporter import export_postman
from projectstack.exporters.grafana_exporter import export_influxdb, export_grafana_dashboard


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
@click.version_option("0.1.0", prog_name="pstack")
def cli():
    """ProjectStack — HAR to jMeter JMX generator."""


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("har_file", type=click.Path(exists=True, readable=True))
def parse(har_file):
    """Parse a HAR file and print a summary of all captured requests."""
    click.echo(f"Parsing: {har_file}")
    try:
        requests = parse_har(har_file)
        summarize(requests)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# correlate
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("har_file", type=click.Path(exists=True, readable=True))
@click.option("--no-ollama", is_flag=True, default=False, help="Skip Ollama AI detection (faster).")
@click.option("--min-confidence", default=0, type=int, show_default=True,
              help="Only show values at or above this confidence score (0-99).")
def correlate(har_file, no_ollama, min_confidence):
    """Analyze a HAR file and show all detected dynamic values with confidence scores."""
    click.echo(f"Parsing: {har_file}")
    try:
        requests = parse_har(har_file)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    click.echo(f"Loaded {len(requests)} requests.\n")
    correlated = run_correlation(requests, use_claude=not no_ollama)

    if not correlated:
        click.echo("\nNo correlated values found.")
        return

    annotated = confidence_annotate(correlated)
    filtered = [a for a in annotated if a["confidence_score"] >= min_confidence]

    if not filtered:
        click.echo(f"\nNo values at or above confidence {min_confidence}.")
        return

    click.echo(f"\n{'='*65}")
    click.echo(f"  CORRELATED VALUES ({len(filtered)} found)")
    click.echo(f"{'='*65}")
    for a in filtered:
        cv = a["value"]
        score = a["confidence_score"]
        label = a["confidence_label"]
        click.echo(f"\n  Variable   : {cv.var_name}")
        click.echo(f"  Confidence : {score}/99  [{label}]")
        click.echo(f"  Source     : Request #{cv.source_request_index + 1} ({cv.extract_from})")
        click.echo(f"  Key        : {cv.extract_key}")
        click.echo(f"  Value      : {cv.extract_value[:60]}...")
        click.echo(f"  Used in    : Requests {[i+1 for i in cv.used_in_requests]}")
        click.echo(f"  Pass       : {cv.detection_pass}")
        click.echo(f"  Reason     : {cv.reason}")


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("har_file", type=click.Path(exists=True, readable=True))
@click.option("--output", "-o", default=None, help="Output JMX file path (default: <har_name>.jmx)")
@click.option("--threads", "-t", default=1, show_default=True, help="Number of virtual users.")
@click.option("--ramp-up", "-r", default=1, show_default=True, help="Ramp-up period in seconds.")
@click.option("--loops", "-l", default=1, show_default=True, help="Number of loop iterations.")
@click.option("--no-ollama", is_flag=True, default=False, help="Skip Ollama AI detection.")
@click.option("--min-confidence", default=0, type=int, show_default=True,
              help="Only correlate values at or above this confidence score.")
@click.option("--test-name", default="ProjectStack Test", show_default=True,
              help="Test plan name shown in jMeter.")
@click.option("--no-correlation-markers", is_flag=True,
              help="Do not fail samples sent with an extractor's NOT_FOUND default (used by the results health check).")
def generate(har_file, output, threads, ramp_up, loops, no_ollama, min_confidence, test_name, no_correlation_markers):
    """Generate a jMeter JMX script from a HAR file."""
    output_path = output or str(Path(har_file).with_suffix(".jmx"))

    click.echo(f"Parsing: {har_file}")
    try:
        requests = parse_har(har_file)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    click.echo(f"Loaded {len(requests)} requests.")
    correlated = run_correlation(requests, use_claude=not no_ollama)

    if min_confidence > 0:
        annotated = confidence_annotate(correlated)
        correlated = [a["value"] for a in annotated if a["confidence_score"] >= min_confidence]
        click.echo(f"After confidence filter (>={min_confidence}): {len(correlated)} values retained.")

    click.echo("Grouping requests into transactions...")
    transactions = group_transactions(requests)
    click.echo(f"  {len(transactions)} transaction(s) identified.")

    click.echo(f"\nGenerating JMX: {output_path}")
    generate_jmx(
        requests=requests,
        correlated_values=correlated,
        output_path=output_path,
        test_name=test_name,
        threads=threads,
        ramp_up=ramp_up,
        loops=loops,
        transactions=transactions,
        correlation_markers=not no_correlation_markers,
    )

    click.echo("Validating JMX structure...")
    valid = validate_and_report(output_path)
    if valid:
        click.echo(f"Done. Open {output_path} in jMeter.")
    else:
        click.echo("JMX written with warnings — review the errors above before running in jMeter.", err=True)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("jmx_file", type=click.Path(exists=True, readable=True))
def validate(jmx_file):
    """Validate that a JMX file is well-formed and passes all ProjectStack structural checks."""
    valid = validate_and_report(jmx_file)
    if not valid:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("jmx_file", type=click.Path(exists=True, readable=True))
def edit(jmx_file):
    """Open a JMX file in your default editor ($EDITOR, or nano as fallback)."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    click.echo(f"Opening {jmx_file} in {editor} ...")
    try:
        subprocess.run([editor, jmx_file], check=True)
    except FileNotFoundError:
        click.echo(
            f"Editor '{editor}' not found. Set $EDITOR to your preferred editor.",
            err=True,
        )
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        click.echo(f"Editor exited with code {exc.returncode}.", err=True)
        raise SystemExit(exc.returncode)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("jmx_file", type=click.Path(exists=True, readable=True))
@click.option("--threads", "-t", default=1, show_default=True, type=int, help="Override virtual user count.")
@click.option("--ramp-up", "-r", default=1, show_default=True, type=int, help="Override ramp-up period in seconds.")
@click.option("--loops", "-l", default=1, show_default=True, type=int, help="Override loop iteration count.")
@click.option("--output-dir", "-o", default=None, help="Directory for JTL results (default: temp dir).")
@click.option("--timeout", default=3600, show_default=True, help="Max seconds to wait for completion.")
def run(jmx_file, threads, ramp_up, loops, output_dir, timeout):
    """Run a JMX test plan with jMeter and display metrics."""
    jmeter = find_jmeter()
    if not jmeter:
        click.echo(
            "Error: jMeter not found.\nInstall it or set JMETER_BIN=/path/to/jmeter.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"jMeter : {jmeter}")
    click.echo(f"JMX    : {jmx_file}")
    if threads:
        click.echo(f"Threads: {threads}")
    if ramp_up:
        click.echo(f"Ramp-up: {ramp_up}s")
    if loops:
        click.echo(f"Loops  : {loops}")
    click.echo("\nRunning test... (this may take a while)\n")

    try:
        result = run_jmeter(
            jmx_path=jmx_file,
            output_dir=output_dir,
            threads=threads,
            ramp_up=ramp_up,
            loops=loops,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    except Exception as exc:
        click.echo(f"jMeter failed: {exc}", err=True)
        raise SystemExit(1)

    if result["returncode"] != 0:
        click.echo("jMeter exited with errors. Last log lines:", err=True)
        for line in result["stderr"].splitlines()[-10:]:
            click.echo(f"  {line}", err=True)
        click.echo(f"\nFull log: {result['log_path']}", err=True)
        raise SystemExit(1)

    click.echo(f"Results : {result['jtl_path']}")

    try:
        summary = parse_jtl(result["jtl_path"])
        print_summary(summary)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Warning: could not parse results — {exc}", err=True)


# ---------------------------------------------------------------------------
# export (sub-group)
# ---------------------------------------------------------------------------

@cli.group()
def export():
    """Export results or HAR data to other formats."""


@export.command("postman")
@click.argument("har_file", type=click.Path(exists=True, readable=True))
@click.option("--output", "-o", default=None, help="Output JSON file (default: <har_name>_postman.json)")
@click.option("--name", default="ProjectStack Collection", show_default=True,
              help="Collection name shown in Postman.")
def export_postman_cmd(har_file, output, name):
    """Export a HAR file as a Postman Collection v2.1 JSON."""
    output_path = output or str(Path(har_file).with_name(
        Path(har_file).stem + "_postman.json"
    ))
    click.echo(f"Parsing: {har_file}")
    try:
        requests = parse_har(har_file)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    export_postman(requests, output_path, collection_name=name)
    click.echo(f"Postman collection written: {output_path}")
    click.echo(f"  Requests : {len(requests)}")
    click.echo("Import via Postman → File → Import → Upload Files.")


@export.command("grafana")
@click.argument("jtl_file", type=click.Path(exists=True, readable=True))
@click.option("--output-dir", "-o", default=None,
              help="Directory for output files (default: same dir as JTL).")
@click.option("--test-name", default="pstack_test", show_default=True,
              help="Tag added to every InfluxDB line for filtering in Grafana.")
def export_grafana_cmd(jtl_file, output_dir, test_name):
    """Export JTL results to InfluxDB line protocol and Grafana dashboard JSON."""
    click.echo(f"Parsing JTL: {jtl_file}")
    try:
        summary = parse_jtl(jtl_file)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    out_dir = Path(output_dir) if output_dir else Path(jtl_file).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    lp_path = out_dir / "metrics.lp"
    dash_path = out_dir / "grafana_dashboard.json"

    export_influxdb(summary, lp_path, test_name=test_name)
    export_grafana_dashboard(summary, dash_path)

    click.echo(f"\nInfluxDB line protocol : {lp_path}")
    click.echo(f"  Push with: influx write -b <bucket> -f {lp_path}")
    click.echo(f"\nGrafana dashboard JSON : {dash_path}")
    click.echo("  Import via: Grafana → Dashboards → Import → Upload JSON file.")
    print_summary(summary)


# ---------------------------------------------------------------------------
# ui
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--port", default=8501, show_default=True, help="Port to serve the UI on.")
@click.option("--host", default="localhost", show_default=True, help="Host to bind to.")
def ui(port, host):
    """Launch the ProjectStack web UI (requires streamlit)."""
    import shutil
    from pathlib import Path

    if not shutil.which("streamlit"):
        click.echo(
            "Error: streamlit is not installed.\n"
            "Install it with: pip install streamlit pandas",
            err=True,
        )
        raise SystemExit(1)

    app_path = Path(__file__).parent.parent / "ui" / "app.py"
    click.echo(f"Starting UI at http://{host}:{port}")
    try:
        subprocess.run(
            [
                "streamlit", "run", str(app_path),
                f"--server.port={port}",
                f"--server.address={host}",
                "--server.headless=true",
            ],
            check=True,
        )
    except KeyboardInterrupt:
        click.echo("\nUI stopped.")


# ---------------------------------------------------------------------------
# Results analysis: analyze / runs / db
# ---------------------------------------------------------------------------

def _results_db(clickhouse_url):
    from projectstack.results.db import ResultsDBError, connect
    try:
        return connect(url=clickhouse_url)
    except ResultsDBError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)


_CH_OPTION = click.option(
    "--clickhouse", "clickhouse_url", default=None, metavar="URL",
    help="clickhouse://user:pass@host:8123/db — overrides settings and PSTACK_CLICKHOUSE_*.",
)


class _AnalyzeCommand(click.Command):
    """With --ci, command-line mistakes exit with 4 (analysis error) instead of click's 2 (a regression in CI)."""

    def make_context(self, info_name, args, parent=None, **extra):
        ci = "--ci" in args
        try:
            return super().make_context(info_name, args, parent=parent, **extra)
        except click.UsageError as exc:
            if ci:
                exc.exit_code = 4
            raise


@cli.command(cls=_AnalyzeCommand)
@click.argument("jtl_file", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--steady", default=None, metavar="START:END",
              help="Analysis window, e.g. 5m:35m (offsets from the first sample) or epoch ms.")
@click.option("--sla", default=None, metavar="SPEC", help="e.g. p95=3000,err=1,p95[Login]=2000,tps>=50")
@click.option("--tag", default="", help="Build or label for this run (also used in the run id).")
@click.option("--scenario", default=None, help="Test scenario name (default: the file name).")
@click.option("--props", type=click.Path(exists=True, dir_okay=False), default=None,
              help="user.properties the test ran with — needed for CSV files without a header row.")
@click.option("--stats", type=click.Choice(["both", "parent", "child"]), default="both", show_default=True,
              help="Transaction Controller parents, their child requests, or both (shown separately).")
@click.option("--max-bad-rows", default=0, show_default=True, help="Malformed rows to skip before failing.")
@click.option("--baseline", default=None, metavar="RUN|last-green",
              help="Compare with this run, or with the latest passing run of the same scenario.")
@click.option("--regression", default=None, metavar="SPEC",
              help="Regression thresholds, e.g. p95=10%,p95ms=50,err=1,samples=100,load=10.")
@click.option("--jmx", type=click.Path(exists=True, dir_okay=False), default=None,
              help="The test plan the run used: correlation problems are mapped to its extractors.")
@click.option("--ci", is_flag=True,
              help="CI mode: write report.html and summary.md, print the summary, exit 0 pass · 1 SLA breach · "
                   "2 regression · 3 both · 4 analysis error.")
@click.option("--artifacts", type=click.Path(file_okay=False), default=None,
              help="With --ci: also copy report.html, summary.md, findings.json and comparison.json here.")
@click.option("--no-llm", is_flag=True, help="With --ci: write the report narrative without AI.")
@click.option("--all", "show_all", is_flag=True, help="List every label, not just the first 25.")
@click.option("--json", "as_json", is_flag=True, help="Print the metrics as JSON instead of tables.")
@_CH_OPTION
def analyze(jtl_file, steady, sla, tag, scenario, props, stats, max_bad_rows, baseline, regression, jmx, ci,
            artifacts, no_llm, show_all, as_json, clickhouse_url):
    """Load a JMeter result file (CSV or XML, optionally .gz) into ClickHouse and summarise it."""
    import json as _json
    from projectstack.results import ci as ci_mode
    from projectstack.results.analyze import analyze_file
    from projectstack.results.db import ResultsDBError
    from projectstack.results.ingest import IngestError
    from projectstack.results.summary import render, render_comparison

    def fail(message):
        click.echo(f"Error: {message}", err=True)
        if ci and artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            (Path(artifacts) / "summary.md").write_text(ci_mode.failure_markdown(message, jtl_file))
        raise SystemExit(ci_mode.EXIT_ERROR if ci else 2)

    log = (lambda msg: click.echo(msg, err=True)) if (ci or not as_json) else (lambda msg: None)
    try:
        from projectstack.results.db import connect
        db = connect(url=clickhouse_url)
        result = analyze_file(
            jtl_file, steady=steady, sla=sla, tag=tag, scenario=scenario, props=props, stats=stats,
            max_bad_rows=max_bad_rows, baseline=baseline, regression=regression, jmx=jmx, db=db, progress=log,
        )
    except (IngestError, ResultsDBError, ValueError) as exc:
        fail(exc)
    except Exception as exc:          # in CI any crash must still exit 4, with the reason
        if not ci:
            raise
        fail(f"{type(exc).__name__}: {exc}")

    if as_json and not ci:
        click.echo(_json.dumps({"run_id": result.run_id, "meta": result.meta, "metrics": result.metrics,
                                "findings": result.findings, "comparison": result.comparison}, indent=2))
        return
    table = render(result.meta, result.metrics, stats, limit=None if show_all else 25, findings=result.findings)
    if result.comparison:
        table += "\n\n" + render_comparison(result.comparison, limit=None if show_all else 25)
    if not ci:
        click.echo("")
        click.echo(table)
        click.echo(f"Artifacts: {result.folder}")
        return

    # CI: tables to stderr, the Markdown summary to stdout, exit code from SLA and regressions
    click.echo("\n" + table, err=True)
    report_path = result.folder / "report.html"
    try:
        from projectstack.results.report import build_report
        built = build_report(str(result.folder), audience="tech", formats=("html",), use_llm=not no_llm,
                             out=str(report_path), progress=log)
        for w in built.warnings:
            click.echo(f"Warning: {w}", err=True)
    except Exception as exc:          # a report problem must not hide the verdict
        click.echo(f"Warning: report.html was not written: {exc}", err=True)
        report_path = None
    code = ci_mode.exit_code(result.findings, result.comparison)
    dest = Path(artifacts) if artifacts else result.folder
    link_target = dest / "report.html" if report_path else None
    summary = ci_mode.summary_markdown(result.findings, result.comparison, code, link_target)
    (result.folder / "summary.md").write_text(summary)
    if artifacts:
        ci_mode.copy_artifacts(result.folder, dest)
    click.echo(summary)
    click.echo(f"Artifacts: {dest}", err=True)
    raise SystemExit(code)


@cli.command()
@click.argument("run_a")
@click.argument("run_b")
@click.option("--regression", default=None, metavar="SPEC",
              help="Regression thresholds, e.g. p95=10%,p95ms=50,err=1,samples=100,load=10.")
@click.option("--format", "out_format", type=click.Choice(["text", "md", "json"]), default="text", show_default=True)
@click.option("--all", "show_all", is_flag=True, help="List every label.")
@click.option("--out", default=None, metavar="PATH", help="Write to a file instead of the terminal.")
@_CH_OPTION
def compare(run_a, run_b, regression, out_format, show_all, out, clickhouse_url):
    """Compare run B with baseline run A (A: run id, unique prefix, or last-green).

    Statistics cover each run's steady state. A label has regressed when its p95 or error rate
    worsened beyond the thresholds; regressions only count when the load was comparable.
    """
    import json as _json
    from projectstack.config import load_settings
    from projectstack.results import store
    from projectstack.results.ci import comparison_markdown
    from projectstack.results.compare import (
        LAST_GREEN, CompareError, RegressionThresholds, compare as compare_runs, find_run, load_run,
    )
    from projectstack.results.summary import render_comparison

    settings = load_settings()
    runs_dir = Path(settings["runs_dir"])
    conn = {}

    def get_db():
        if "db" not in conn:
            conn["db"] = _results_db(clickhouse_url)
        return conn["db"]

    def load(run_id):
        try:
            return load_run(run_id, runs_dir)
        except CompareError:
            return load_run(run_id, runs_dir, get_db())

    try:
        thresholds = RegressionThresholds.from_settings(settings, regression)
        current = load(find_run(run_b, runs_dir, get_db))
        if run_a.strip().lower() == LAST_GREEN:
            base_id = store.last_green(get_db(), current.meta.get("scenario", ""), current.run_id)
            if not base_id:
                raise CompareError(f"No passing run of scenario {current.meta.get('scenario')!r} before "
                                   f"{current.run_id}")
        else:
            base_id = find_run(run_a, runs_dir, get_db)
        if base_id == current.run_id:
            raise CompareError("Both arguments are the same run")
        comparison = compare_runs(load(base_id), current, thresholds)
    except (CompareError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)
    limit = None if show_all else 25
    text = (_json.dumps(comparison, indent=2) if out_format == "json" else
            comparison_markdown(comparison, limit) if out_format == "md" else render_comparison(comparison, limit))
    if out:
        Path(out).write_text(text if text.endswith("\n") else text + "\n")
        click.echo(out)
    else:
        click.echo(text)


@cli.group()
def runs():
    """Analysed runs stored in ClickHouse."""


@runs.command("list")
@click.option("--limit", default=20, show_default=True)
@click.option("--scenario", default=None, help="Only runs of this scenario.")
@click.option("--json", "as_json", is_flag=True)
@_CH_OPTION
def runs_list(limit, scenario, as_json, clickhouse_url):
    """List analysed runs, newest first."""
    import json as _json
    from projectstack.results.store import list_runs
    from projectstack.results.summary import render_runs

    rows = list_runs(_results_db(clickhouse_url), limit=limit, scenario=scenario)
    if as_json:
        click.echo(_json.dumps(rows, indent=2, default=str))
    else:
        click.echo(render_runs(rows))


@runs.command("show")
@click.argument("run_ref")
@click.option("--all", "show_all", is_flag=True, help="List every label.")
@click.option("--json", "as_json", is_flag=True)
@_CH_OPTION
def runs_show(run_ref, show_all, as_json, clickhouse_url):
    """Show a run's summary (RUN_REF: run id or a unique prefix)."""
    import json as _json
    from projectstack.config import load_settings
    from projectstack.results import store
    from projectstack.results.summary import render

    db = _results_db(clickhouse_url)
    try:
        run_id = store.resolve_run(db, run_ref)
    except LookupError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)
    runs_dir = Path(load_settings()["runs_dir"])
    meta = store.read_artifact(runs_dir, run_id, "meta") or store.get_meta(db, run_id)
    metrics = store.read_artifact(runs_dir, run_id, "metrics")
    findings = store.read_artifact(runs_dir, run_id, "findings")
    if metrics is None:
        click.echo(f"Error: metrics.json for {run_id} is missing from {runs_dir / run_id}", err=True)
        raise SystemExit(2)
    if as_json:
        click.echo(_json.dumps({"run_id": run_id, "meta": meta, "metrics": metrics, "findings": findings}, indent=2))
    else:
        click.echo(render(meta, metrics, meta.get("stats_mode", "both"), limit=None if show_all else 25,
                          findings=findings))


@runs.command("delete")
@click.argument("run_ref")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@_CH_OPTION
def runs_delete(run_ref, yes, clickhouse_url):
    """Delete a run's samples, registry entry and artifacts."""
    from projectstack.config import load_settings
    from projectstack.results import store

    db = _results_db(clickhouse_url)
    try:
        run_id = store.resolve_run(db, run_ref)
    except LookupError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)
    if not yes:
        click.confirm(f"Delete run {run_id} and all its samples?", abort=True)
    store.delete_run(db, run_id, Path(load_settings()["runs_dir"]))
    click.echo(f"Deleted {run_id}")


@cli.command()
@click.argument("run_ref")
@click.option("--audience", type=click.Choice(["tech", "exec"]), default="tech", show_default=True,
              help="tech: full engineering report · exec: one-page summary for decision makers.")
@click.option("--format", "formats", type=click.Choice(["html", "md", "both"]), default="html", show_default=True)
@click.option("--no-llm", is_flag=True, help="Write the narrative with rules; nothing is sent to an AI service.")
@click.option("--provider", "provider_spec", default=None, metavar="NAME[/MODEL]",
              help="AI provider for this report, e.g. claude, gemini, groq/llama-3.3-70b-versatile "
                   "(default: the 'report' task setting).")
@click.option("--strict-numbers", is_flag=True,
              help="Fail (exit 3) if the AI narrative cites numbers that are not in findings.json, after one retry.")
@click.option("--fresh", is_flag=True, help="Ask the AI again instead of reusing the saved narrative.")
@click.option("--out", default=None, metavar="PATH",
              help="File or folder to write to (default: the run's folder); - prints Markdown to stdout.")
@_CH_OPTION
def report(run_ref, audience, formats, no_llm, provider_spec, strict_numbers, fresh, out, clickhouse_url):
    """Write a report for an analysed run (RUN_REF: run id, unique prefix, or a findings.json path).

    Reports are built from the run's findings.json only; the AI provider, if used, receives that
    document and nothing else.
    """
    from projectstack.results.narrative import NumberCheckFailed
    from projectstack.results.report import ReportError, build_report

    def resolve_in_db(ref):
        from projectstack.results import store
        return store.resolve_run(_results_db(clickhouse_url), ref)

    if out == "-" and formats != "md":
        formats = "md"
    chosen = ("html", "md") if formats == "both" else (formats,)
    try:
        result = build_report(run_ref, audience=audience, formats=chosen, use_llm=not no_llm,
                              provider_spec=provider_spec, strict=strict_numbers, out=out, fresh=fresh,
                              resolve_in_db=resolve_in_db, progress=lambda msg: click.echo(msg, err=True))
    except NumberCheckFailed as exc:
        click.echo(f"Error: {exc}", err=True)
        for u in exc.narrative.numbers.unverified if exc.narrative.numbers else []:
            click.echo(f"  “{u['text']}” in {u['field']}", err=True)
        raise SystemExit(3)
    except (ReportError, LookupError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)
    for w in result.warnings:
        click.echo(f"Warning: {w}", err=True)
    if result.stdout is not None:
        click.echo(result.stdout, nl=False)
        return
    n = result.narrative
    if n.source == "llm":
        how = f"{n.provider} ({n.prompt}){', saved narrative reused' if result.reused else ''}"
        click.echo(f"Narrative: {how} — {n.numbers.describe() if n.numbers else 'not checked'}", err=True)
    else:
        click.echo("Narrative: rule-based (no AI)", err=True)
    for path in result.files:
        click.echo(str(path))


@cli.command()
@click.argument("run_ref")
@click.argument("question")
@click.option("--provider", "provider_spec", default=None, metavar="NAME[/MODEL]",
              help="AI provider, e.g. claude or gemini (default: the 'ask' task setting).")
@click.option("--max-queries", default=8, show_default=True, help="Most queries the model may run.")
@click.option("--show-queries", is_flag=True, help="Print every query with its result.")
@click.option("--json", "as_json", is_flag=True, help="Print the whole exchange as JSON.")
@_CH_OPTION
def ask(run_ref, question, provider_spec, max_queries, show_queries, as_json, clickhouse_url):
    """Ask a question about a run (RUN_REF: run id or unique prefix).

    The AI starts from the run's findings.json and may run read-only SQL over the run's samples
    (SELECT only, this run only, 200 rows and 10 seconds per query). It answers citing its queries;
    the numbers it states are checked against findings.json and the query results.
    """
    import json as _json
    from projectstack.config import load_settings
    from projectstack.llm.base import LLMConfigError, LLMError
    from projectstack.llm.registry import provider_for_task
    from projectstack.results import ask as ask_mod
    from projectstack.results import store
    from projectstack.results.report import ReportError, locate
    from projectstack.results.sandbox import RunSandbox

    settings = load_settings()
    database = _results_db(clickhouse_url)
    try:
        folder, doc = locate(run_ref, Path(settings["runs_dir"]), lambda ref: store.resolve_run(database, ref))
        run_id = store.resolve_run(database, doc["run"]["run_id"])        # its samples must be in ClickHouse
        provider = provider_for_task("ask", settings, provider_spec)
        if provider.name == "ollama":
            click.echo(f"Warning: {ask_mod.SMALL_MODEL_WARNING}", err=True)
        sandbox = RunSandbox(database.cfg, run_id, settings)
        click.echo(f"Asking {provider.label} …", err=True)
        result = ask_mod.ask(doc, question, provider=provider, sandbox=sandbox, max_queries=max_queries,
                             progress=lambda msg: click.echo(f"  {msg}", err=True))
    except (ReportError, LookupError, LLMConfigError, LLMError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)
    ask_mod.save(result, folder)
    if as_json:
        click.echo(_json.dumps({**result.as_dict(), "saved": str(result.saved)}, indent=2, default=str))
        return
    click.echo("")
    click.echo(result.answer)
    click.echo("")
    for q in result.queries:
        status = f"{len(q.rows)} row(s){' (cut)' if q.truncated else ''}, {q.seconds:.2f}s" if q.ok else q.error
        click.echo(f"Q{q.n} — {status}")
        click.echo("    " + "\n    ".join(q.sql.splitlines()))
        if show_queries and q.ok:
            for line in q.as_text().splitlines()[1:21]:
                click.echo(f"      {line}")
    note = " (query limit reached)" if result.stopped == "query_limit" else ""
    click.echo(f"\nAnswered by {result.provider} with prompt {result.prompt}{note} · {len(result.queries)} "
               f"quer{'y' if len(result.queries) == 1 else 'ies'} · numbers: "
               f"{result.numbers.describe('findings.json or the query results') if result.numbers else 'not checked'}")
    click.echo(f"Saved: {result.saved}", err=True)


@cli.command()
@click.argument("run_ref")
@click.option("--jmx", type=click.Path(exists=True, dir_okay=False), default=None,
              help="The test plan the run used (default: the plan.jmx saved by analyze --jmx).")
@click.option("--json", "as_json", is_flag=True)
@_CH_OPTION
def health(run_ref, jmx, as_json, clickhouse_url):
    """Correlation health check for a run: missing values, 401/403s, login redirects, auth errors.

    With the test plan, each problem is mapped to the extractor most likely responsible.
    """
    import json as _json
    from dataclasses import asdict
    from projectstack.config import load_settings
    from projectstack.results import health as health_mod
    from projectstack.results import store
    from projectstack.results.plan import PlanError, load_plan

    database = _results_db(clickhouse_url)
    try:
        run_id = store.resolve_run(database, run_ref)
        saved = Path(load_settings()["runs_dir"]) / run_id / "plan.jmx"
        plan_path = jmx or (saved if saved.exists() else None)
        plan = load_plan(plan_path) if plan_path else None
    except (LookupError, PlanError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(2)
    found = health_mod.check_run(database, run_id, plan)
    if as_json:
        click.echo(_json.dumps({"run_id": run_id, "plan": str(plan_path) if plan_path else None,
                                "findings": [asdict(f) for f in found]}, indent=2, default=str))
        return
    click.echo(f"Correlation health of {run_id}" + (f" (plan: {plan_path})" if plan_path else
                                                     " (no plan: pass --jmx to map problems to extractors)"))
    if not found:
        click.echo("No signs of correlation problems: no NOT_FOUND values sent, no 401/403 onset, "
                   "no login redirects, no authentication errors.")
        return
    for f in found:
        click.echo(f"\n{'✖' if f.severity == 'critical' else '▲'} {f.title}")
        click.echo(f"  {f.detail}")
        for item in f.evidence.get("labels", [])[:10]:
            extra = ", ".join(item.get("variables") or []) or item.get("message") or item.get("example_url") or ""
            click.echo(f"    {item['label']}: {item['samples']:,} samples{f' — {extra}' if extra else ''}")
        for m in f.evidence.get("mapping", [])[:6]:
            for x in m["extractors"]:
                err = f", which failed {x['source_error_pct']:g}%" if x.get("source_error_pct") else ""
                click.echo(f"    {m['var']} ← {x['description']}{err}")


@cli.group()
def db():
    """Results database (ClickHouse)."""


@db.command("status")
@_CH_OPTION
def db_status(clickhouse_url):
    """Check the ClickHouse connection and show what is stored."""
    database = _results_db(clickhouse_url)
    version = database.rows("SELECT version()")[0][0]
    click.echo(f"Connected to ClickHouse {version} at {database.cfg.display} as {database.cfg.user}")
    rows = database.rows(
        "SELECT name, formatReadableSize(total_bytes) FROM system.tables "
        "WHERE database = {d:String} ORDER BY name", {"d": database.db})
    for name, size in rows:
        # count() honours deleted runs; system.tables keeps counting them until parts merge
        n = database.rows(f"SELECT count() FROM {database.db}.{name}")[0][0]
        click.echo(f"  {name:<20} {n:>14,} rows  {size or '0 B':>12} on disk")
    runs_n = database.rows(f"SELECT uniqExact(run_id) FROM {database.db}.runs FINAL WHERE status = 'ready'")[0][0]
    click.echo(f"  {runs_n} analysed run(s); raw samples kept {database.cfg.retention_days} days")


if __name__ == "__main__":
    cli()
