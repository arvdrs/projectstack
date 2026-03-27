"""
ProjectStack CLI entry point.

Usage:
    pstack parse <har_file>       # Parse and summarize a HAR file (Phase 1)
    pstack generate <har_file>    # Generate JMX from HAR (Phase 3)
    pstack run <jmx_file>         # Run a JMX test (Phase 4)
"""

import click
from projectstack.parser.har_parser import parse_har, summarize


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
