"""
ProjectStack CLI entry point.

Usage:
    pstack parse <har_file>              # Parse and summarize a HAR file
    pstack correlate <har_file>          # Run correlation engine and preview results
    pstack generate <har_file>           # Generate JMX from HAR (Phase 3)
    pstack run <jmx_file>                # Run a JMX test (Phase 4)
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import click
from projectstack.parser.har_parser import parse_har, summarize
from projectstack.correlator.engine import run_correlation
from projectstack.generator.jmx_generator import generate_jmx


@click.group()
@click.version_option("0.1.0", prog_name="pstack")
def cli():
    """ProjectStack — HAR to jMeter JMX generator."""


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


@cli.command()
@click.argument("har_file", type=click.Path(exists=True, readable=True))
@click.option("--no-ollama", is_flag=True, default=False, help="Skip Ollama AI detection (faster).")
def correlate(har_file, no_ollama):
    """Analyze a HAR file and show all detected dynamic values."""
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

    click.echo(f"\n{'='*60}")
    click.echo(f"  CORRELATED VALUES ({len(correlated)} found)")
    click.echo(f"{'='*60}")
    for cv in correlated:
        click.echo(f"\n  Variable : {cv.var_name}")
        click.echo(f"  Source   : Request #{cv.source_request_index + 1} ({cv.extract_from})")
        click.echo(f"  Key      : {cv.extract_key}")
        click.echo(f"  Value    : {cv.extract_value[:60]}...")
        click.echo(f"  Used in  : Requests {[i+1 for i in cv.used_in_requests]}")
        click.echo(f"  Pass     : {cv.detection_pass}")
        click.echo(f"  Reason   : {cv.reason}")


@cli.command()
@click.argument("har_file", type=click.Path(exists=True, readable=True))
@click.option("--output", "-o", default=None, help="Output JMX file path (default: <har_name>.jmx)")
@click.option("--threads", "-t", default=1, show_default=True, help="Number of virtual users.")
@click.option("--ramp-up", "-r", default=1, show_default=True, help="Ramp-up period in seconds.")
@click.option("--loops", "-l", default=1, show_default=True, help="Number of loop iterations.")
@click.option("--no-ollama", is_flag=True, default=False, help="Skip Ollama AI detection.")
@click.option("--test-name", default="ProjectStack Test", show_default=True, help="Test plan name shown in jMeter.")
def generate(har_file, output, threads, ramp_up, loops, no_ollama, test_name):
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

    click.echo(f"Valid jMeter JMX file.")
    click.echo(f"  Samplers : {len(samplers)}")
    click.echo(f"  Extractors : {len(root.findall('.//JSR223PostProcessor'))}")
