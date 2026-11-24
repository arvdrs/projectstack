"""
JMeter HTML reports from an uploaded JTL: input preparation (every format JMeter's
report generator cannot read as it is becomes a plain CSV with a header row), the
automatic graph granularity, JMeter's errors in plain words, and the web API
(upload → background job → view / download / list / delete) with the real JMeter.
"""

import csv
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from projectstack.results.ingest import DEFAULT_HEADER
from projectstack.runner.html_report import (
    ReportError, ReportOptions, auto_granularity_ms, generate_html_report, jmeter_error, prepare_input, time_span_ms,
)
from projectstack.runner.jmeter_runner import find_jmeter
from tests.jtl_synth import JMETER_COLUMNS, START_MS, flow_samples, write_csv, write_xml

needs_jmeter = pytest.mark.skipif(find_jmeter() is None, reason="JMeter not installed")


def _rows():
    return flow_samples(users=3, iterations=4, error_every=5)


def _xml_samples(rows):
    # XML nests a redirect ("Search-0") inside its request rather than listing it separately
    return [r for r in rows if r["label"] != "Search-0"]


def _read(path: Path) -> list[list[str]]:
    return list(csv.reader(path.open(newline="")))


# ---------------------------------------------------------------------------
# Input preparation
# ---------------------------------------------------------------------------

def test_csv_with_header_is_used_as_is(tmp_path):
    src = write_csv(tmp_path / "r.jtl", _rows())
    assert prepare_input(src, tmp_path, log=lambda m: None) == src
    assert not list(tmp_path.glob("*.csv"))


@pytest.mark.parametrize("kind", ["no-header", "tab", "gzip"])
def test_other_csv_forms_become_plain_csv_with_header(tmp_path, kind):
    rows = _rows()
    reference = _read(write_csv(tmp_path / "ref.jtl", rows))
    src = {
        "no-header": lambda: write_csv(tmp_path / "r.jtl", rows, header=False),
        "tab": lambda: write_csv(tmp_path / "r.jtl", rows, delimiter="\t"),
        "gzip": lambda: write_csv(tmp_path / "r.jtl.gz", rows, gz=True),
    }[kind]()
    messages = []
    work = tmp_path / "work"
    work.mkdir()
    out = prepare_input(src, work, log=messages.append)
    assert out == work / "r.csv"                        # the upload's name: JMeter shows it on the dashboard
    got = _read(out)
    assert got[0] == (JMETER_COLUMNS if kind != "no-header" else DEFAULT_HEADER)
    assert got[1:] == reference[1:]                     # every sample, unchanged
    assert messages                                     # the user is told what was done


def test_xml_becomes_csv_with_every_sample_and_no_extra_columns(tmp_path):
    rows = _rows()
    src = write_xml(tmp_path / "r.xml", _xml_samples(rows))
    out = prepare_input(src, tmp_path, log=lambda m: None)
    got = _read(out)
    header = got[0]
    assert "psKind" not in header and "Hostname" not in header      # JMeter rejects these in a header row
    assert len(got) - 1 == len(rows)                                # nested sub-results are kept, as in a CSV JTL
    label = header.index("label")
    assert sorted(r[label] for r in got[1:]) == sorted(r["label"] for r in rows)
    failed = [r for r in got[1:] if r[header.index("success")] == "false"]
    assert failed and all(r[header.index("failureMessage")] for r in failed)


def test_not_a_result_file_is_rejected(tmp_path):
    (tmp_path / "notes.txt").write_text("hello,world\n1,2\n")
    with pytest.raises(ReportError, match="epoch milliseconds"):
        prepare_input(tmp_path / "notes.txt", tmp_path, log=lambda m: None)
    (tmp_path / "empty.jtl").write_text("")
    with pytest.raises(ReportError, match="empty"):
        prepare_input(tmp_path / "empty.jtl", tmp_path, log=lambda m: None)


def test_xml_without_samples_is_rejected(tmp_path):
    (tmp_path / "r.xml").write_text('<?xml version="1.0"?>\n<testResults version="1.2">\n</testResults>\n')
    with pytest.raises(ReportError, match="no samples"):
        prepare_input(tmp_path / "r.xml", tmp_path, log=lambda m: None)


# ---------------------------------------------------------------------------
# Granularity
# ---------------------------------------------------------------------------

def test_time_span_from_head_and_tail(tmp_path):
    rows = [{**_rows()[0], "timeStamp": START_MS + i * 1000} for i in range(30_000)]   # > 64 KB each way
    rows[5]["timeStamp"] = START_MS - 5000             # out of order near the start
    path = write_csv(tmp_path / "r.jtl", rows)
    assert time_span_ms(path) == (30_000 - 1) * 1000 + 5000


@pytest.mark.parametrize("span_s, expected_ms", [
    (None, 2_000), (60, 2_000), (400, 2_000), (401, 5_000), (30 * 60, 10_000), (60 * 60, 30_000),
    (8 * 3600, 300_000), (30 * 24 * 3600, 1_800_000),
])
def test_auto_granularity_keeps_graphs_readable(span_s, expected_ms):
    assert auto_granularity_ms(span_s * 1000 if span_s else None) == expected_ms


# ---------------------------------------------------------------------------
# JMeter's errors
# ---------------------------------------------------------------------------

def test_jmeter_errors_in_plain_words(tmp_path):
    noise = "WARN StatusConsoleListener The use of package scanning to locate plugins is deprecated\n"
    regex = ("An error occurred: Error while processing samples: Consumer failed with message :Consumer failed "
             "with message :Unclosed character class near index 9\n([unclosed\n         ^\n")
    msg = jmeter_error(noise + regex, tmp_path / "missing.log", ReportOptions(label_filter="([unclosed"))
    assert msg.startswith("The label filter is not a valid regular expression: Unclosed character class")
    assert "Consumer failed" not in msg
    msg = jmeter_error(noise + "An error occurred: null\n", tmp_path / "missing.log", ReportOptions(label_filter="^X$"))
    assert msg == "No samples have a label matching the filter '^X$'."
    (tmp_path / "j.log").write_text("2026-01-01 10:00:00,000 ERROR o.a.j.JMeter: Out of disk\n")
    assert jmeter_error(noise, tmp_path / "j.log", ReportOptions()).endswith("o.a.j.JMeter: Out of disk")


# ---------------------------------------------------------------------------
# With JMeter
# ---------------------------------------------------------------------------

@needs_jmeter
def test_xml_and_csv_give_the_same_report(tmp_path):
    rows = _rows()
    csv_report = generate_html_report(write_csv(tmp_path / "r.jtl", rows), tmp_path / "csv", tmp_path / "w1",
                                      log=lambda m: None)
    xml_report = generate_html_report(write_xml(tmp_path / "r.xml", _xml_samples(rows)), tmp_path / "xml",
                                      tmp_path / "w2", log=lambda m: None)
    assert (tmp_path / "csv" / "index.html").is_file()
    s = csv_report["summary"]
    # JMeter's Total leaves out Transaction Controller parents ("Login"), so it counts 84 of 96 rows
    assert s["samples"] == len(rows) - 12 and s["errors"] == 2 and s["labels"] == 8
    assert json.loads((tmp_path / "csv" / "statistics.json").read_text()) == \
        json.loads((tmp_path / "xml" / "statistics.json").read_text())
    assert csv_report["options"].granularity_ms is None                       # JMeter's own default, as `jmeter -g`
    assert '"granularity": 60000' in (tmp_path / "csv" / "content" / "js" / "graph.js").read_text()
    auto = generate_html_report(write_csv(tmp_path / "a.jtl", rows), tmp_path / "auto", tmp_path / "w3",
                                ReportOptions(auto_granularity=True), log=lambda m: None)
    assert auto["options"].granularity_ms == 2_000                             # chosen from the test's length
    assert xml_report["summary"] == s


@needs_jmeter
def test_options_reach_the_dashboard(tmp_path):
    out = tmp_path / "out"
    result = generate_html_report(
        write_csv(tmp_path / "r.jtl", _rows()), out, tmp_path / "w", log=lambda m: None,
        options=ReportOptions(title="Checkout <nightly>", apdex_satisfied_ms=200, apdex_tolerated_ms=800,
                              granularity_ms=10_000, label_filter="^(Home|Search)$"))
    assert result["summary"]["labels"] == 2 and result["summary"]["samples"] == 24
    index = (out / "index.html").read_text()
    assert "<title>Checkout &lt;nightly&gt;</title>" in index                  # JMeter escapes the title
    dashboard = (out / "content" / "js" / "dashboard.js").read_text()
    assert ', 200, 800, "Total"]' in dashboard                                 # Apdex T and F
    assert '"granularity": 10000' in (out / "content" / "js" / "graph.js").read_text()


@needs_jmeter
def test_label_filter_matching_nothing(tmp_path):
    with pytest.raises(ReportError, match="No samples have a label matching"):
        generate_html_report(write_csv(tmp_path / "r.jtl", _rows()), tmp_path / "out", tmp_path / "w",
                             ReportOptions(label_filter="^Nothing$"), log=lambda m: None)


# ---------------------------------------------------------------------------
# Web API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from projectstack.api import html_reports_api
    from projectstack.api import main as api
    monkeypatch.setattr(html_reports_api, "REPORTS_DIR", tmp_path / "html-reports")
    return TestClient(api.app)


def _wait(client, report_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        meta = client.get(f"/api/html-reports/{report_id}").json()
        if meta["status"] in ("done", "failed"):
            return meta
        time.sleep(0.2)
    raise AssertionError("report generation did not finish")


@needs_jmeter
def test_upload_generate_view_download_delete(client, tmp_path):
    path = write_xml(tmp_path / "nightly.xml", _xml_samples(_rows()))
    with path.open("rb") as f:
        res = client.post("/api/html-reports", files={"file": ("nightly.xml", f)},
                          data={"title": "Nightly", "apdex_satisfied": "300"})
    assert res.status_code == 200, res.text
    started = res.json()
    assert started["status"] in ("queued", "running", "done")
    assert started["options"]["apdex_tolerated_ms"] == 1200                    # 4 × T when only T is given

    meta = _wait(client, started["id"])
    assert meta["status"] == "done", meta
    assert meta["title"] == "Nightly" and meta["source_file"] == "nightly.xml"
    assert meta["summary"]["samples"] == 84 and meta["options"]["granularity_ms"] == 60_000
    assert any("XML" in p for p in meta["progress"]) and meta["progress"][-1].startswith("Done:")

    folder = tmp_path / "html-reports" / meta["id"]
    assert not (folder / "upload").exists() and not (folder / "work").exists()  # inputs removed
    assert (folder / "jmeter.log").is_file()

    page = client.get(meta["view_url"])
    assert page.status_code == 200 and "Nightly" in page.text
    assert '"nightly.csv"' in page.text                                        # source file named after the upload
    assert client.get(f"/api/html-reports/{meta['id']}/view/content/js/dashboard.js").status_code == 200
    assert client.get(f"/api/html-reports/{meta['id']}/view/../meta.json").status_code == 404
    assert client.get(f"/api/html-reports/{meta['id']}/view/%2e%2e/meta.json").status_code == 404

    zipped = client.get(meta["download_url"])
    assert zipped.status_code == 200
    assert 'filename="Nightly-jmeter-report.zip"' in zipped.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert "Nightly/index.html" in names and "Nightly/statistics.json" in names

    listed = client.get("/api/html-reports").json()["reports"]
    assert [r["id"] for r in listed] == [meta["id"]]

    assert client.delete(f"/api/html-reports/{meta['id']}").status_code == 200
    assert not folder.exists()
    assert client.get(f"/api/html-reports/{meta['id']}").status_code == 404


@needs_jmeter
def test_rejected_uploads(client, tmp_path):
    bad = client.post("/api/html-reports", files={"file": ("notes.txt", b"hello,world\n1,2\n")})
    assert bad.status_code == 400 and "epoch milliseconds" in bad.json()["detail"]
    empty = client.post("/api/html-reports", files={"file": ("empty.jtl", b"")})
    assert empty.status_code == 400 and "empty" in empty.json()["detail"]
    jtl = write_csv(tmp_path / "r.jtl", _rows()).read_bytes()
    apdex = client.post("/api/html-reports", files={"file": ("r.jtl", jtl)},
                        data={"apdex_satisfied": "800", "apdex_tolerated": "400"})
    assert apdex.status_code == 400 and "greater than" in apdex.json()["detail"]
    gran = client.post("/api/html-reports", files={"file": ("r.jtl", jtl)}, data={"granularity": "500"})
    assert gran.status_code == 400 and "granularity must be between" in gran.json()["detail"]
    assert not list((tmp_path / "html-reports").iterdir())                      # nothing left behind


@needs_jmeter
def test_failed_generation_is_reported(client, tmp_path):
    jtl = write_csv(tmp_path / "r.jtl", _rows()).read_bytes()
    res = client.post("/api/html-reports", files={"file": ("r.jtl", jtl)}, data={"label_filter": "^Nothing$"})
    meta = _wait(client, res.json()["id"])
    assert meta["status"] == "failed" and "No samples have a label matching" in meta["error"]
    assert meta["view_url"] is None and meta["download_url"] is None
    assert client.get(f"/api/html-reports/{meta['id']}/download").status_code == 404


def test_interrupted_generation_shows_as_failed(client, tmp_path):
    from projectstack.api import html_reports_api
    folder = tmp_path / "html-reports" / "20261124-101500-abcdef"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({
        "id": folder.name, "title": "t", "source_file": "r.jtl", "source_size": 1, "created_at": "2026-11-24T10:15:00+00:00",
        "status": "running", "progress": [], "error": None, "options": {}, "summary": None, "seconds": None,
    }))
    assert folder.name not in html_reports_api._active
    meta = client.get(f"/api/html-reports/{folder.name}").json()
    assert meta["status"] == "failed" and "Interrupted" in meta["error"]
    assert client.get("/api/html-reports/not-an-id").status_code == 404
    assert client.delete(f"/api/html-reports/{folder.name}").status_code == 200


def test_rewrite_never_overwrites_the_source(tmp_path):
    src = write_csv(tmp_path / "r.csv", _rows(), header=False)
    before = src.read_bytes()
    out = prepare_input(src, tmp_path, log=lambda m: None)
    assert out != src and src.read_bytes() == before
    assert _read(out)[1:] == list(csv.reader(io.StringIO(before.decode())))
