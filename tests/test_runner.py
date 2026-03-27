"""Tests for the jMeter runner and metrics parser."""

import csv
import time
from pathlib import Path

import pytest

from projectstack.runner.jmeter_runner import find_jmeter, _override_thread_settings
from projectstack.runner.metrics_parser import parse_jtl, MetricsSummary


# ---------------------------------------------------------------------------
# jMeter finder
# ---------------------------------------------------------------------------

def test_find_jmeter_returns_path():
    path = find_jmeter()
    # On this system jMeter is installed — should be found
    assert path is not None
    assert Path(path).exists()


# ---------------------------------------------------------------------------
# Thread override helper
# ---------------------------------------------------------------------------

def test_override_thread_settings(tmp_path):
    """Override writes a temp JMX with updated ThreadGroup values."""
    from projectstack.parser.har_parser import HarRequest
    from projectstack.generator.jmx_generator import generate_jmx
    import xml.etree.ElementTree as ET

    req = HarRequest(
        method="GET", url="https://example.com/",
        headers={}, query_params=[], body=None, body_mime_type=None,
        status_code=200, response_headers={}, response_body=None,
        response_mime_type=None, cookies=[],
    )
    jmx = str(tmp_path / "test.jmx")
    generate_jmx([req], [], output_path=jmx, threads=1, ramp_up=1, loops=1)

    modified = _override_thread_settings(jmx, threads=10, ramp_up=30, loops=5)
    try:
        root = ET.parse(modified).getroot()
        tg = root.find(".//ThreadGroup")
        assert tg.find("stringProp[@name='ThreadGroup.num_threads']").text == "10"
        assert tg.find("stringProp[@name='ThreadGroup.ramp_time']").text == "30"
        loop = tg.find(".//elementProp[@name='ThreadGroup.main_controller']")
        assert loop.find("stringProp[@name='LoopController.loops']").text == "5"
    finally:
        Path(modified).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Metrics parser
# ---------------------------------------------------------------------------

def _write_jtl(path: Path, rows: list[dict]) -> None:
    fieldnames = ["timeStamp", "elapsed", "label", "responseCode", "success"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _ts():
    return int(time.time() * 1000)


def test_parse_jtl_basic(tmp_path):
    now = _ts()
    jtl = tmp_path / "results.jtl"
    _write_jtl(jtl, [
        {"timeStamp": now,        "elapsed": 100, "label": "GET /login",  "responseCode": "200", "success": "true"},
        {"timeStamp": now + 200,  "elapsed": 200, "label": "POST /login", "responseCode": "200", "success": "true"},
        {"timeStamp": now + 500,  "elapsed": 500, "label": "GET /data",   "responseCode": "500", "success": "false"},
    ])
    summary = parse_jtl(jtl)
    assert summary.total_requests == 3
    assert summary.passed == 2
    assert summary.failed == 1
    assert abs(summary.error_rate - 33.3) < 0.5


def test_parse_jtl_response_times(tmp_path):
    now = _ts()
    jtl = tmp_path / "results.jtl"
    times = [100, 200, 300, 400, 500]
    rows = [
        {"timeStamp": now + i * 100, "elapsed": t, "label": "GET /", "responseCode": "200", "success": "true"}
        for i, t in enumerate(times)
    ]
    _write_jtl(jtl, rows)
    summary = parse_jtl(jtl)
    assert summary.min_ms == 100
    assert summary.max_ms == 500
    assert summary.avg_ms == 300.0
    assert summary.p90_ms >= 400


def test_parse_jtl_per_sampler_breakdown(tmp_path):
    now = _ts()
    jtl = tmp_path / "results.jtl"
    _write_jtl(jtl, [
        {"timeStamp": now,       "elapsed": 100, "label": "Login",    "responseCode": "200", "success": "true"},
        {"timeStamp": now + 100, "elapsed": 200, "label": "Login",    "responseCode": "200", "success": "true"},
        {"timeStamp": now + 200, "elapsed": 300, "label": "Dashboard","responseCode": "200", "success": "true"},
    ])
    summary = parse_jtl(jtl)
    assert "Login" in summary.samplers
    assert "Dashboard" in summary.samplers
    assert summary.samplers["Login"].total == 2
    assert summary.samplers["Dashboard"].total == 1


def test_parse_jtl_throughput(tmp_path):
    now = _ts()
    jtl = tmp_path / "results.jtl"
    # 10 requests spread over ~1 second
    rows = [
        {"timeStamp": now + i * 100, "elapsed": 50, "label": "GET /", "responseCode": "200", "success": "true"}
        for i in range(10)
    ]
    _write_jtl(jtl, rows)
    summary = parse_jtl(jtl)
    assert summary.throughput > 0


def test_parse_jtl_file_not_found():
    with pytest.raises(FileNotFoundError):
        parse_jtl("/nonexistent/results.jtl")


def test_parse_jtl_empty_file(tmp_path):
    jtl = tmp_path / "empty.jtl"
    jtl.write_text("timeStamp,elapsed,label,responseCode,success\n")
    with pytest.raises(ValueError, match="no data rows"):
        parse_jtl(jtl)
