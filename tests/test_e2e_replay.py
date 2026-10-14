"""
End-to-end: record a flow against a live mock app, correlate it, and replay the
generated JMX with real JMeter. Skipped when JMeter is not installed.
"""

import socket
import threading
import time

import pytest

uvicorn = pytest.importorskip("uvicorn")
pytest.importorskip("httpx")

from projectstack.correlator.engine import run_correlation
from projectstack.parser.har_parser import parse_har
from projectstack.runner.jmeter_runner import find_jmeter
from projectstack.runner.replay import replay_and_fix

from tests.har_recorder import record_checkout_flow
from tests.mock_app import app

needs_jmeter = pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")


@pytest.fixture(scope="module")
def server_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def recorded(server_url, tmp_path):
    har = tmp_path / "flow.har"
    record_checkout_flow(server_url, har)
    return parse_har(har)


def _by_value(cvs, needle):
    return next((cv for cv in cvs if needle(cv)), None)


def test_engine_detects_every_dynamic_pattern(recorded):
    cvs = run_correlation(recorded, use_ai=False)
    kinds = {cv.extractor for cv in cvs}
    assert {"css", "header_regex", "json", "cookie_header", "regex", "function"} <= kinds

    # A token in plain text gets a regex that cannot capture across quotes or markup
    nonce = _by_value(cvs, lambda c: c.var_name.startswith("C_NONCE"))
    assert nonce.extractor == "regex" and r"([^\s\"'<>]+?)" in nonce.extractor_config["regex"]

    order_id = _by_value(cvs, lambda c: c.extractor == "json" and "?(@.name" in (c.extractor_config or {}).get("path", ""))
    assert order_id is not None, "list item should be selected by name, not by index"

    xsrf = _by_value(cvs, lambda c: c.extractor == "cookie_header")
    assert xsrf.extractor_config["url_decode"] is True

    names = {cv.var_name for cv in cvs}
    assert "C_BEARER_TOKEN" in names and "C_ORDER_NO" in names
    # Plain words must not be treated as dynamic values
    assert not any(cv.extract_value in ("callback", "Bearer") for cv in cvs)


@needs_jmeter
def test_replay_passes_with_detected_correlations(recorded, tmp_path):
    cvs = run_correlation(recorded, use_ai=False)
    result = replay_and_fix(recorded, cvs, max_iterations=2, work_dir=tmp_path / "replay")
    final = result["final"]
    assert final["failed"] == 0, final["results"]
    assert len(final["verified"]) >= 6
    assert final["added"] == []


@needs_jmeter
def test_replay_restores_missing_correlations(recorded, tmp_path):
    cvs = run_correlation(recorded, use_ai=False)
    dropped = {"C_CSRF_TOKEN", "C_ITEMS_ID", "C_ORDER_NO"}
    partial = [cv for cv in cvs if cv.var_name not in dropped]
    assert len(partial) == len(cvs) - len(dropped)

    result = replay_and_fix(recorded, partial, max_iterations=5, work_dir=tmp_path / "replay")
    first = result["iterations"][0]
    assert first["failed"] > 0
    restored = {a["recorded"] for it in result["iterations"] for a in it["added"]}
    for name in dropped:
        original = next(cv for cv in cvs if cv.var_name == name)
        assert original.extract_value[:80] in restored, f"{name} was not re-detected by replay"
    assert result["final"]["failed"] == 0, result["final"]["results"]
    assert result["unverified_changes"] is False


@pytest.mark.skipif(__import__("shutil").which("k6") is None, reason="k6 not installed")
def test_k6_export_replays_the_flow(recorded, tmp_path):
    import subprocess
    from projectstack.exporter.k6_exporter import export_k6

    script = tmp_path / "flow.k6.js"
    script.write_text(export_k6(recorded, run_correlation(recorded, use_ai=False)))
    out = subprocess.run(["k6", "run", "--quiet", "--vus", "1", "--iterations", "2", str(script)],
                         capture_output=True, text=True, timeout=120)
    assert "checks_failed......: 0.00%" in out.stdout, out.stdout + out.stderr
