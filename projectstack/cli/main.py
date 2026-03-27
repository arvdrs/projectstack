"""
ProjectStack CLI entry point.

Usage:
    pstack parse <har_file>              # Parse and summarize a HAR file
    pstack correlate <har_file>          # Run correlation engine and preview results
    pstack generate <har_file>           # Generate JMX from HAR (Phase 3)
    pstack run <jmx_file>                # Run a JMX test (Phase 4)
"""

import click
from projectstack.parser.har_parser import parse_har, summarize
from projectstack.correlator.engine import run_correlation


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
