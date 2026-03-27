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
    correlated = run_correlation(requests, use_ollama=not no_ollama)

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
def generate(har_file, output, threads, ramp_up, loops, no_ollama, min_confidence, test_name):
    """Generate a jMeter JMX script from a HAR file."""
    output_path = output or str(Path(har_file).with_suffix(".jmx"))

    click.echo(f"Parsing: {har_file}")
    try:
        requests = parse_har(har_file)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    click.echo(f"Loaded {len(requests)} requests.")
    correlated = run_correlation(requests, use_ollama=not no_ollama)

    if min_confidence > 0:
        annotated = confidence_annotate(correlated)
        correlated = [a["value"] for a in annotated if a["confidence_score"] >= min_confidence]
        click.echo(f"After confidence filter (>={min_confidence}): {len(correlated)} values retained.")

    click.echo(f"\nGenerating JMX: {output_path}")
    generate_jmx(
        requests=requests,
        correlated_values=correlated,
        output_path=output_path,
        test_name=test_name,
        threads=threads,
        ramp_up=ramp_up,
        loops=loops,
    )
    click.echo(f"Done. Open {output_path} in jMeter.")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("jmx_file", type=click.Path(exists=True, readable=True))
def validate(jmx_file):
    """Validate that a JMX file is well-formed XML and has the expected jMeter structure."""
    try:
        tree = ET.parse(jmx_file)
    except ET.ParseError as exc:
        click.echo(f"Invalid XML: {exc}", err=True)
        raise SystemExit(1)

    root = tree.getroot()
    errors = []

    if root.tag != "jmeterTestPlan":
        errors.append(f"Root element is '{root.tag}', expected 'jmeterTestPlan'")
    if root.find(".//TestPlan") is None:
        errors.append("Missing <TestPlan> element")
    if root.find(".//ThreadGroup") is None:
        errors.append("Missing <ThreadGroup> element")

    samplers = root.findall(".//HTTPSamplerProxy")
    if not samplers:
        errors.append("No <HTTPSamplerProxy> elements found")

    if errors:
        click.echo("Validation failed:")
        for e in errors:
            click.echo(f"  - {e}", err=True)
        raise SystemExit(1)

    click.echo("Valid jMeter JMX file.")
    click.echo(f"  Samplers   : {len(samplers)}")
    click.echo(f"  Extractors : {len(root.findall('.//JSR223PostProcessor'))}")


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
@click.option("--threads", "-t", default=None, type=int, help="Override virtual user count.")
@click.option("--ramp-up", "-r", default=None, type=int, help="Override ramp-up period in seconds.")
@click.option("--loops", "-l", default=None, type=int, help="Override loop iteration count.")
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
