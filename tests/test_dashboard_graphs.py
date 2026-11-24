"""
The five report graphs as JPEGs captured from JMeter's HTML report (Running Vusers, Hits Per
Second, Throughput, Response Time, Error): the capture itself, the HTML Report page's graphs,
an analysed run's graphs, and the promise that they are the same pictures: the same JTL gives
byte-identical JPEGs on both pages.
"""

import hashlib
import io
import json
import random
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from projectstack.runner import dashboard_graphs as dg
from projectstack.runner.html_report import ReportOptions, generate_html_report
from projectstack.runner.jmeter_runner import find_jmeter
from tests.jtl_synth import START_MS, flow_samples, sample, write_csv
from tests.test_results_web import _wait, ch_db, client  # noqa: F401  (fixtures: isolated ClickHouse database)

needs_tools = pytest.mark.skipif(find_jmeter() is None or dg.find_chrome() is None,
                                 reason="needs JMeter and Chrome/Chromium")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def many_labels_jtl(path: Path) -> Path:
    """160 labels and 7 thread groups: with this many, JMeter's line colours depend on the file's order."""
    rng = random.Random(3)
    rows = []
    for i in range(6000):
        label = f"TX {rng.randint(0, 39):02d} {['login', 'search', 'cart', 'pay'][i % 4]}"
        rows.append(sample(START_MS + i * 50, rng.randint(50, 900), label, f"Journey {i % 7} 1-{i % 9 + 1}", 9,
                           ok=rng.random() > 0.02))
    return write_csv(path, rows)


def test_slug_from_name():
    assert dg.slug_from_name("running-vusers.jpg") == "running-vusers"
    assert dg.slug_from_name("errors.JPEG") == "errors"
    for bad in ("errors.png", "latency.jpg", "errors", "../errors.jpg"):
        assert dg.slug_from_name(bad) is None


def test_clear_errors_without_chrome_or_pages(tmp_path, monkeypatch):
    with pytest.raises(dg.GraphCaptureError, match="no graph pages"):
        dg.capture_graphs(tmp_path, tmp_path / "out")
    pages = tmp_path / "content" / "pages"
    pages.mkdir(parents=True)
    (pages / "OverTime.html").write_text("")
    (pages / "Throughput.html").write_text("")
    monkeypatch.setattr(dg, "find_chrome", lambda: None)
    with pytest.raises(dg.GraphCaptureError, match="Chrome or Chromium"):
        dg.capture_graphs(tmp_path, tmp_path / "out")
    with pytest.raises(dg.GraphCaptureError, match="not been captured"):
        dg.zip_graphs(tmp_path / "out", "x")


@needs_tools
def test_capture(tmp_path):
    jtl = write_csv(tmp_path / "r.jtl", flow_samples(users=3, iterations=4, error_every=5))
    generate_html_report(jtl, tmp_path / "report", tmp_path / "w", log=lambda m: None)
    files = dg.capture_graphs(tmp_path / "report", tmp_path / "graphs")
    assert list(files) == dg.SLUGS
    for path in files.values():
        img = Image.open(path)
        assert img.format == "JPEG" and img.width > 2000 and img.height > 800       # a panel at 2× for print
    again = dg.capture_graphs(tmp_path / "report", tmp_path / "graphs2")
    assert all(_sha(files[s].read_bytes()) == _sha(again[s].read_bytes()) for s in dg.SLUGS)   # deterministic
    names = zipfile.ZipFile(io.BytesIO(dg.zip_graphs(tmp_path / "graphs", "Nightly"))).namelist()
    assert names == [f"Nightly_{s}.jpg" for s in dg.SLUGS]
    assert json.loads((tmp_path / "graphs" / dg.MANIFEST).read_text())["errors"] == "codesPerSecond"


def test_graphs_from_other_panels_do_not_count(tmp_path):
    for s in dg.SLUGS:
        (tmp_path / f"{s}.jpg").write_bytes(b"jpeg")
    assert dg.graph_files(tmp_path) == {}                                # captured before panels.json existed
    (tmp_path / dg.MANIFEST).write_text(json.dumps({**dg.PANELS, "errors": "totalTPS"}))
    assert dg.graph_files(tmp_path) == {}                                # the Error Graph's first panel
    (tmp_path / dg.MANIFEST).write_text(json.dumps(dg.PANELS))
    assert list(dg.graph_files(tmp_path)) == dg.SLUGS
    (tmp_path / "errors.jpg").unlink()
    assert dg.graph_files(tmp_path) == {}


def test_error_graph_is_codes_per_second():
    assert dg.PANELS["errors"] == "codesPerSecond"                      # one line per response code, errors included


# ---------------------------------------------------------------------------
# Both pages
# ---------------------------------------------------------------------------

def _html_report(c, jtl: Path) -> dict:
    with jtl.open("rb") as f:
        rid = c.post("/api/html-reports", files={"file": (jtl.name, f)}).json()["id"]
    deadline = time.monotonic() + 180
    while (meta := c.get(f"/api/html-reports/{rid}").json())["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert meta["status"] == "done" and not meta["graphs_error"], meta
    return meta


def _run_graphs(c, run_id: str) -> dict:
    deadline = time.monotonic() + 180
    while (state := c.get(f"/api/results/runs/{run_id}/graphs").json()).get("status") == "building":
        assert time.monotonic() < deadline
        time.sleep(0.3)
    return state


@needs_tools
def test_same_jtl_same_jpegs_on_both_pages(client, tmp_path, monkeypatch):  # noqa: F811
    from projectstack.api import html_reports_api
    from projectstack.config import load_settings
    monkeypatch.setattr(html_reports_api, "REPORTS_DIR", tmp_path / "html-reports")
    jtl = many_labels_jtl(tmp_path / "checkout.jtl")

    # HTML Report page: the graphs are captured with the report
    report = _html_report(client, jtl)
    assert dg.graph_files(tmp_path / "html-reports" / report["id"] / "graphs")
    page = {s: client.get(f"{report['graphs_url']}/{s}.jpg") for s in dg.SLUGS}
    assert all(r.status_code == 200 and r.headers["content-type"] == "image/jpeg" for r in page.values())

    # Analyse Results: JMeter's report is built from the uploaded file alongside the analysis
    with jtl.open("rb") as f:
        job = _wait(client, client.post("/api/results/analyze", files={"file": ("checkout.jtl", f)},
                                        data={"scenario": "checkout"}).json()["id"])
    assert job["status"] == "done", job
    state = _run_graphs(client, job["run_id"])
    assert state["status"] == "ready" and state["source"] == "original", state
    for slug in dg.SLUGS:
        res = client.get(f"{state['graphs_url']}/{slug}.jpg?download=1")
        assert res.status_code == 200
        assert _sha(res.content) == _sha(page[slug].content), f"{slug} differs from the HTML Report's"
        assert res.headers["content-disposition"] == f'attachment; filename="checkout_{job["run_id"]}_{slug}.jpg"'
    assert not list(Path(tmp_path / "uploads").rglob("*.jtl"))                 # the upload is still removed

    # Download all: the same five JPEGs in one ZIP, on both pages
    zipped = client.get(state["zip_url"])
    assert zipped.headers["content-disposition"] == f'attachment; filename="checkout_{job["run_id"]}_graphs.zip"'
    zf = zipfile.ZipFile(io.BytesIO(zipped.content))
    assert [n.rsplit("_", 1)[-1] for n in zf.namelist()] == [f"{s}.jpg" for s in dg.SLUGS]
    assert all(_sha(zf.read(n)) == _sha(page[s].content) for n, s in zip(zf.namelist(), dg.SLUGS))
    report_zip = zipfile.ZipFile(io.BytesIO(client.get(report["graphs_zip_url"]).content))
    assert report_zip.namelist() == [f"checkout_{s}.jpg" for s in dg.SLUGS]

    # Graphs captured from the Error Graph's first panel are captured again, from the run's own report
    folder = Path(load_settings()["runs_dir"]) / job["run_id"]
    (folder / "graphs" / dg.MANIFEST).write_text(json.dumps({**dg.PANELS, "errors": "totalTPS"}))
    assert client.get(f"/api/results/runs/{job['run_id']}/graphs").json()["status"] in ("building", "ready")
    again = _run_graphs(client, job["run_id"])
    assert again["status"] == "ready" and again["source"] == "original", again
    assert _sha(client.get(f"{again['graphs_url']}/errors.jpg").content) == _sha(page["errors"].content)

    # JPEG only
    assert client.get(f"{state['graphs_url']}/errors.png").status_code == 404
    assert client.get(f"{report['graphs_url']}/errors.png").status_code == 404

    # A run whose original file is gone: built from the stored samples, with the same numbers
    original = json.loads(_graph_data(folder / "jmeter-report"))
    import shutil
    shutil.rmtree(folder / "graphs"); shutil.rmtree(folder / "jmeter-report")
    assert client.get(f"/api/results/runs/{job['run_id']}/graphs").json()["status"] == "none"
    assert client.get(f"{state['graphs_url']}/errors.jpg").status_code == 404
    assert client.post(f"/api/results/runs/{job['run_id']}/graphs").json()["status"] == "building"
    rebuilt = _run_graphs(client, job["run_id"])
    assert rebuilt.get("status") == "ready" and rebuilt["source"] == "stored-samples", rebuilt
    stored = json.loads(_graph_data(folder / "jmeter-report"))
    for graph in ("activeThreadsOverTime", "hitsPerSecond", "bytesThroughputOverTime", "codesPerSecond"):
        assert stored[graph] == original[graph], graph                   # every series and every point, exactly
    # JMeter keeps running averages, so the last of 15-16 digits of a mean depends on the samples' order
    assert stored["responseTimesOverTime"].keys() == original["responseTimesOverTime"].keys()
    for label, points in original["responseTimesOverTime"].items():
        other = stored["responseTimesOverTime"][label]
        assert [t for t, _ in other] == [t for t, _ in points]
        assert all(abs(a - b) <= 1e-9 * max(1.0, abs(b)) for (_, a), (_, b) in zip(other, points)), label
    for slug in ("running-vusers", "hits-per-second", "throughput", "errors"):
        assert _sha(client.get(f"{rebuilt['graphs_url']}/{slug}.jpg").content) == _sha(page[slug].content), slug


def _graph_data(report: Path) -> str:
    """Each over-time graph's series as {label: points}, from the report's graph.js."""
    import re
    js = (report / "content" / "js" / "graph.js").read_text()
    out = {}
    for name in ("activeThreadsOverTime", "hitsPerSecond", "bytesThroughputOverTime", "codesPerSecond", "responseTimesOverTime"):
        m = re.search(rf"var {name}Infos\s*=\s*\{{\s*data\s*:\s*", js)
        data, _ = json.JSONDecoder().raw_decode(js, m.end())
        out[name] = {s["label"]: sorted(map(tuple, s["data"])) for s in data["result"]["series"]}
    return json.dumps(out, sort_keys=True)


@needs_tools
def test_html_report_without_captured_graphs_captures_on_request(tmp_path, monkeypatch):
    """Reports made before the graphs were captured with them get them when first asked for."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from projectstack.api import html_reports_api
    from projectstack.api import main as api
    monkeypatch.setattr(html_reports_api, "REPORTS_DIR", tmp_path / "html-reports")
    c = TestClient(api.app)
    report = _html_report(c, write_csv(tmp_path / "r.jtl", flow_samples(users=2, iterations=3)))
    import shutil
    shutil.rmtree(tmp_path / "html-reports" / report["id"] / "graphs")
    res = c.get(f"{report['graphs_url']}/running-vusers.jpg?download=1")
    assert res.status_code == 200 and Image.open(io.BytesIO(res.content)).format == "JPEG"
    assert res.headers["content-disposition"] == 'attachment; filename="r_running-vusers.jpg"'
    assert res.headers["cache-control"] == "no-cache"                   # a browser checks again: graphs can be recaptured
    assert c.get(f"{report['graphs_url']}/latency.jpg").status_code == 404

    # Captured from the Error Graph's first panel (Total Transactions Per Second): captured again when asked for
    graphs = tmp_path / "html-reports" / report["id"] / "graphs"
    current = (graphs / "errors.jpg").read_bytes()
    (graphs / "errors.jpg").write_bytes(b"old picture")
    (graphs / dg.MANIFEST).write_text(json.dumps({**dg.PANELS, "errors": "totalTPS"}))
    assert c.get(f"{report['graphs_url']}/errors.jpg?rev=2").content == current
    assert json.loads((graphs / dg.MANIFEST).read_text()) == dg.PANELS
