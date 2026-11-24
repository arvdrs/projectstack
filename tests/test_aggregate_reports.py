"""
JMeter Aggregate Reports from an uploaded JTL: the two tables (APIs and transactions) are
byte for byte what JMeter's own Aggregate Report saves for the same samples (checked by running
JMeter's classes, tests/jmeter_aggregate.groovy), the split between the tables, the notes,
every input format, the Excel workbook, and the web API.
"""

import io
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from projectstack.results.xlsx import Sheet, workbook
from projectstack.runner.aggregate_report import (
    COLUMNS, AggregateError, AggregateOptions, build_aggregate, saved_values, shown_values, table_csv,
)
from projectstack.runner.jmeter_runner import find_jmeter
from tests.jtl_synth import START_MS, TC_MSG, flow_samples, sample, write_csv, write_xml


def _jmeter_home() -> Path | None:
    jmeter = find_jmeter()
    if not jmeter or not shutil.which("java"):
        return None
    home = Path(os.path.realpath(jmeter)).parent.parent
    return home if (home / "lib" / "ext" / "ApacheJMeter_core.jar").is_file() else None


needs_jmeter = pytest.mark.skipif(_jmeter_home() is None, reason="JMeter (and Java) not installed")
HARNESS = Path(__file__).with_name("jmeter_aggregate.groovy")
EMPTY_TC = "Number of samples in transaction : 0, number of failing samples : 0"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _rows() -> list[dict]:
    """A journey with every case: requests, a Transaction Controller, a redirect written before its
    request ("Search-0"), a request that only looks like a sub-result ("Step-1"), errors, plus labels
    with a comma and quotes, five samples (JMeter's float percentages matter), under one sample a
    second, a single 0 ms sample (JMeter's Infinity), empty transactions and a negative elapsed time."""
    rows = flow_samples(users=4, iterations=6, error_every=4)
    t, thread = START_MS + 900_000, "Thread Group 1-1"
    rows += [sample(t + i * 700, 40 + 7 * i, 'Lookup, "quoted"', thread, 4) for i in range(6)]
    rows += [sample(t + 10_000 + i * 1000, e, "Tiny", thread, 4) for i, e in enumerate((10, 20, 30, 40, 50))]
    rows += [sample(t + 20_000, 120, "Rare", thread, 4), sample(t + 920_000, 95, "Rare", thread, 4, ok=False, code="503")]
    rows += [sample(t + 30_000, 0, "Instant", thread, 4)]
    for i in range(4):          # a Transaction Controller that sometimes runs nothing (an If Controller inside it)
        ran = i % 2 == 0
        tc = sample(t + 40_000 + i * 2000, 300 if ran else 0, "Optional flow", thread, 4,
                    msg=TC_MSG.format(n=1, f=0) if ran else EMPTY_TC)
        tc["dataType"] = ""
        rows.append(tc)
    cut = sample(t + 60_000, -(t + 60_000), "Login", thread, 4, msg=TC_MSG.format(n=1, f=0))   # test stopped mid-transaction
    cut["dataType"] = ""
    return rows + [cut]


def _keep(row: dict, options: AggregateOptions) -> bool:
    """Whether a sample counts at all (the same rules build_aggregate applies)."""
    if int(row["elapsed"]) < 0:
        return False
    return options.include_empty_transactions or not row["responseMessage"].startswith(EMPTY_TC[:36])


def _jmeter(tmp: Path, pairs: list[tuple[Path, Path]]) -> None:
    home = _jmeter_home()
    subprocess.run(
        ["java", "-cp", f"{home}/lib/*:{home}/lib/ext/*", f"-Djmeter.home={home}", "-Djava.awt.headless=true",
         "groovy.ui.GroovyMain", str(HARNESS), *[str(p) for pair in pairs for p in pair]],
        check=True, capture_output=True, cwd=tmp, timeout=300)


# ---------------------------------------------------------------------------
# The numbers: JMeter's own Aggregate Report
# ---------------------------------------------------------------------------

@needs_jmeter
@pytest.mark.parametrize("options", [AggregateOptions(), AggregateOptions(include_subresults=True, include_empty_transactions=True)],
                         ids=["defaults", "everything"])
def test_both_tables_are_what_jmeter_saves(tmp_path, options):
    rows = _rows()
    src = write_csv(tmp_path / "results.jtl", rows)
    report = build_aggregate(src, options, log=lambda m: None)

    # JMeter's Aggregate Report of exactly the samples of each table
    pairs = []
    for key in ("apis", "transactions"):
        labels = {r["label"] for r in report[key]["rows"]}
        part = write_csv(tmp_path / f"{key}.jtl", [r for r in rows if r["label"] in labels and _keep(r, options)])
        pairs.append((part, tmp_path / f"{key}.jmeter.csv"))
    _jmeter(tmp_path, pairs)
    for key, (_, expected) in zip(("apis", "transactions"), pairs):
        assert table_csv(report[key]) == expected.read_text(encoding="utf-8"), key


@needs_jmeter
def test_a_large_file_matches_jmeter(tmp_path):
    from tests.jtl_synth import bulk_csv
    src = bulk_csv(tmp_path / "bulk.jtl", rows=60_000, labels=12, users=20, minutes=10)
    report = build_aggregate(src, log=lambda m: None)
    assert not report["transactions"]["rows"]
    _jmeter(tmp_path, [(src, tmp_path / "jmeter.csv")])
    assert table_csv(report["apis"]) == (tmp_path / "jmeter.csv").read_text(encoding="utf-8")


def test_percentile_lines_use_jmeters_float_percentages(tmp_path):
    rows = [sample(START_MS + i * 1000, e, "Tiny", "T 1-1", 1) for i, e in enumerate((10, 20, 30, 40, 50))]
    row = build_aggregate(write_csv(tmp_path / "r.jtl", rows), log=lambda m: None)["apis"]["rows"][0]
    # round(5 × 0.9f) = round(4.4999998…) = 4, so the 90% Line is the 4th value (exact 0.9 would give the 5th)
    assert (row["median"], row["p90"], row["p95"], row["p99"]) == (30, 40, 50, 50)
    assert (row["average"], row["min"], row["max"]) == (30, 10, 50)


def test_average_is_cut_not_rounded(tmp_path):
    rows = [sample(START_MS + i, e, "A", "T 1-1", 1) for i, e in enumerate((1, 2, 2))]       # mean 1.67
    assert build_aggregate(write_csv(tmp_path / "r.jtl", rows), log=lambda m: None)["apis"]["rows"][0]["average"] == 1


# ---------------------------------------------------------------------------
# The split and the notes
# ---------------------------------------------------------------------------

def test_split_between_apis_and_transactions(tmp_path):
    report = build_aggregate(write_csv(tmp_path / "r.jtl", _rows()), log=lambda m: None)
    apis = [r["label"] for r in report["apis"]["rows"]]
    assert [r["label"] for r in report["transactions"]["rows"]] == ["Login", "Optional flow"]
    assert "Search-0" not in apis                                     # a redirect inside "Search"
    assert {"Home", "Login page", "Login submit", "Search", "Step-1", "Step"} <= set(apis)   # Step-1 is a request
    assert apis.index("Home") < apis.index("Search")                  # the order labels first appear in
    notes = report["notes"]
    assert notes["subresults"] == {"included": False, "labels": ["Search-0"], "samples": 24}
    assert notes["empty_transactions"] == {"included": False, "samples": 2, "labels": ["Optional flow"]}
    assert notes["negative_elapsed"] == 1
    optional = next(r for r in report["transactions"]["rows"] if r["label"] == "Optional flow")
    assert (optional["samples"], optional["min"]) == (2, 300)         # the two empty ones are not counted

    everything = build_aggregate(write_csv(tmp_path / "r.jtl", _rows()),
                                 AggregateOptions(include_subresults=True, include_empty_transactions=True), log=lambda m: None)
    assert "Search-0" in [r["label"] for r in everything["apis"]["rows"]]
    optional = next(r for r in everything["transactions"]["rows"] if r["label"] == "Optional flow")
    assert (optional["samples"], optional["min"]) == (4, 0)
    assert everything["notes"]["empty_transactions"]["samples"] == 0


def test_subresults_are_found_whichever_comes_first(tmp_path):
    rows = flow_samples(users=3, iterations=5)
    first = []                                                        # the request, then its redirect (as JMeter writes)
    for r in rows:
        if r["label"] == "Search" and first and first[-1]["label"] == "Search-0":
            first.insert(len(first) - 1, r)
        else:
            first.append(r)
    assert [r["label"] for r in first].index("Search") < [r["label"] for r in first].index("Search-0")
    for order in (rows, first):
        report = build_aggregate(write_csv(tmp_path / "r.jtl", order), log=lambda m: None)
        assert report["notes"]["subresults"]["labels"] == ["Search-0"]


def test_a_file_without_transactions(tmp_path):
    rows = [r for r in flow_samples(users=2, iterations=2) if not r["responseMessage"].startswith("Number of samples")]
    report = build_aggregate(write_csv(tmp_path / "r.jtl", rows), log=lambda m: None)
    assert report["transactions"] == {"rows": [], "total": None}
    assert report["apis"]["total"]["samples"] == len(rows) - 4        # the four Search-0 redirects are left out


@pytest.mark.parametrize("kind", ["no-header", "tab", "gzip", "xml"])
def test_every_input_format_gives_the_same_tables(tmp_path, kind):
    rows = flow_samples(users=3, iterations=4, error_every=3)
    reference = build_aggregate(write_csv(tmp_path / "ref.jtl", rows), log=lambda m: None)
    src = {
        "no-header": lambda: write_csv(tmp_path / "r.jtl", rows, header=False),
        "tab": lambda: write_csv(tmp_path / "r.jtl", rows, delimiter="\t"),
        "gzip": lambda: write_csv(tmp_path / "r.jtl.gz", rows, gz=True),
        "xml": lambda: write_xml(tmp_path / "r.xml", [r for r in rows if r["label"] != "Search-0"]),   # nested in Search
    }[kind]()
    report = build_aggregate(src, log=lambda m: None)

    def by_label(t):
        return {r["label"]: r for r in t["rows"]}, t["total"]
    for key in ("apis", "transactions"):
        assert by_label(report[key]) == by_label(reference[key]), key
    assert report["notes"]["subresults"]["labels"] == ["Search-0"]


def test_unreadable_files_are_rejected(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello\nworld\n")
    with pytest.raises(AggregateError):
        build_aggregate(bad, log=lambda m: None)
    empty_only = write_csv(tmp_path / "e.jtl", [{**sample(START_MS, 0, "T", "T 1-1", 1), "responseMessage": EMPTY_TC}])
    with pytest.raises(AggregateError, match="empty transaction"):
        build_aggregate(empty_only, log=lambda m: None)


def test_a_cut_off_last_line_is_skipped(tmp_path):
    src = write_csv(tmp_path / "r.jtl", flow_samples(users=2, iterations=2))
    src.write_bytes(src.read_bytes() + f"{START_MS + 99_000},12".encode())        # the test was killed mid-write
    report = build_aggregate(src, log=lambda m: None)
    assert report["notes"]["unreadable_rows"] == 1


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

def _row(**kw):
    base = {"label": "L", "samples": 10, "average": 5, "median": 5, "p90": 9, "p95": 9, "p99": 9, "min": 1, "max": 9,
            "error_rate": 0.0, "throughput": 1.0, "received_kbps": 0.0, "sent_kbps": 0.0}
    return {**base, **kw}


def test_saved_values_follow_jmeters_formats():
    assert saved_values(_row(throughput=0.0311800001, error_rate=0.0021, received_kbps=126.854))[9:] == [
        "0.210%", ".03118", "126.85", "0.00"]                         # "#0.000%", "#.00000" (no leading 0), "#0.00"
    assert saved_values(_row(throughput=None))[10] == "∞"             # a single 0 ms sample
    assert saved_values(_row(received_kbps=0.125, sent_kbps=0.375))[11:] == ["0.12", "0.38"]   # exact ties: half-even
    assert table_csv({"rows": [_row(label='Lookup, "quoted"')], "total": None}).splitlines() == [
        ",".join(COLUMNS), '"Lookup, ""quoted""",10,5,5,9,9,9,1,9,0.000%,1.00000,0.00,0.00']


def test_shown_values_follow_jmeters_table():
    assert shown_values(_row(throughput=31.555977))["throughput"] == "31.6/sec"
    assert shown_values(_row(throughput=0.39))["throughput"] == "23.4/min"
    assert shown_values(_row(throughput=0.0144))["throughput"] == "51.8/hour"
    assert shown_values(_row(throughput=None))["throughput"] == "∞/sec"
    assert shown_values(_row(throughput=2.25))["throughput"] == "2.2/sec"            # an exact tie: half-even
    assert shown_values(_row(error_rate=0.0016893))["error"] == "0.17%"


def _sheet(z: zipfile.ZipFile, n: int) -> list[list[tuple[str | None, str]]]:
    root = ET.fromstring(z.read(f"xl/worksheets/sheet{n}.xml"))
    out = []
    for row in root.find("m:sheetData", NS):
        cells = []
        for c in row:
            if c.get("t") == "inlineStr":
                cells.append((c.get("s"), "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))))
            else:
                v = c.find("m:v", NS)
                cells.append((c.get("s"), v.text if v is not None else ""))
        out.append(cells)
    return out


def test_workbook_parts_and_cells():
    data = workbook([
        Sheet("APIs", ["Label", "Count", "Rate"], [["a & <b>", 3, 0.25], [" spaced\x01", None, float("inf")]],
              [None, "#,##0", "0.00%"], [20, 10, 10], bold_rows={1}),
        Sheet("A/B:C*?[x]" * 4, ["x"], [[1]]),
        Sheet("apis", ["y"], [[2.5]]),
    ])
    z = zipfile.ZipFile(io.BytesIO(data))
    assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml",
            "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml"} <= set(z.namelist())
    for name in z.namelist():
        ET.fromstring(z.read(name))                                    # every part is well-formed XML
    names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet")]
    assert names[0] == "APIs" and len(names[1]) <= 31 and not re.search(r"[\[\]:*?/\\]", names[1])
    assert names[2].lower() != "apis"                                  # sheet names must differ, ignoring case
    rows = _sheet(z, 1)
    assert [v for _, v in rows[0]] == ["Label", "Count", "Rate"]
    assert [v for _, v in rows[1]] == ["a & <b>", "3", "0.25"]
    assert [v for _, v in rows[2]] == [" spaced", "", "inf"]           # control characters dropped; inf as text
    xfs = list(ET.fromstring(z.read("xl/styles.xml")).find("m:cellXfs", NS))
    style = lambda s: (xfs[int(s)].get("numFmtId"), xfs[int(s)].get("fontId"))
    assert style(rows[0][0][0])[1] == "1"                              # the header is bold
    assert style(rows[1][1][0]) == ("3", "0") and style(rows[1][2][0]) == ("10", "0")
    assert style(rows[2][0][0])[1] == "1"                              # bold_rows: the TOTAL row


# ---------------------------------------------------------------------------
# Web API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from projectstack.api import aggregate_reports_api
    from projectstack.api import main as api
    monkeypatch.setattr(aggregate_reports_api, "REPORTS_DIR", tmp_path / "aggregate-reports")
    return TestClient(api.app)


def _wait(client, report_id, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        meta = client.get(f"/api/aggregate-reports/{report_id}").json()
        if meta["status"] not in ("queued", "running"):
            return meta
        time.sleep(0.1)
    raise TimeoutError(report_id)


def test_upload_build_view_download_delete(client, tmp_path):
    src = write_csv(tmp_path / "checkout-nightly.jtl", _rows())
    with src.open("rb") as f:
        r = client.post("/api/aggregate-reports", files={"file": (src.name, f)}, data={"title": "Checkout nightly #142"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    meta = _wait(client, rid)
    assert meta["status"] == "done", meta
    assert meta["counts"] == {"apis": 10, "transactions": 2}
    assert not (tmp_path / "aggregate-reports" / rid / "upload").exists()          # the upload is not kept

    full = client.get(f"/api/aggregate-reports/{rid}?tables=true").json()
    assert full["columns"] == COLUMNS
    assert full["tables"]["apis"]["total"]["shown"]["throughput"].endswith(("/sec", "/min", "/hour"))
    expected = build_aggregate(src, log=lambda m: None)
    for key in ("apis", "transactions"):
        d = client.get(full["downloads"][f"{key}_csv"])
        assert d.status_code == 200
        assert d.content.decode() == table_csv(expected[key])
        assert f'filename="Checkout-nightly-142_aggregate_{key}.csv"' in d.headers["content-disposition"]

    x = client.get(full["downloads"]["xlsx"])
    assert x.status_code == 200 and 'filename="Checkout-nightly-142_aggregate_report.xlsx"' in x.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(x.content))
    names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet")]
    assert names == ["APIs", "Transactions"]
    apis = _sheet(z, 1)
    assert [v for _, v in apis[0]] == COLUMNS
    assert [v for _, v in apis[1]][:3] == [expected["apis"]["rows"][0]["label"], str(expected["apis"]["rows"][0]["samples"]),
                                           str(expected["apis"]["rows"][0]["average"])]
    assert apis[-1][0][1] == "TOTAL"

    listed = client.get("/api/aggregate-reports").json()["reports"]
    assert [m["id"] for m in listed] == [rid] and "tables" not in listed[0]
    assert client.delete(f"/api/aggregate-reports/{rid}").json() == {"deleted": rid}
    assert client.get(f"/api/aggregate-reports/{rid}").status_code == 404


def test_options_reach_the_tables(client, tmp_path):
    src = write_csv(tmp_path / "r.jtl", _rows())
    with src.open("rb") as f:
        rid = client.post("/api/aggregate-reports", files={"file": (src.name, f)},
                          data={"include_subresults": "true", "include_empty_transactions": "true"}).json()["id"]
    meta = _wait(client, rid)
    assert meta["options"] == {"include_subresults": True, "include_empty_transactions": True}
    assert meta["title"] == "r"                                       # the file name when no title is given
    labels = [r["label"] for r in client.get(f"/api/aggregate-reports/{rid}?tables=true").json()["tables"]["apis"]["rows"]]
    assert "Search-0" in labels


def test_rejected_uploads_and_unknown_reports(client, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello\n")
    r = client.post("/api/aggregate-reports", files={"file": ("notes.txt", bad.read_bytes())})
    assert r.status_code == 400
    r = client.post("/api/aggregate-reports", files={"file": ("empty.jtl", b"")})
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    assert not list((tmp_path / "aggregate-reports").glob("*"))      # nothing is left behind
    assert client.get("/api/aggregate-reports/../../etc").status_code == 404
    assert client.get("/api/aggregate-reports/20260101-000000-abcdef").status_code == 404
    assert client.get("/api/aggregate-reports/20260101-000000-abcdef/apis.csv").status_code == 404


def test_no_transactions_table_to_download(client, tmp_path):
    rows = [r for r in flow_samples(users=2, iterations=2) if not r["responseMessage"].startswith("Number of samples")]
    src = write_csv(tmp_path / "r.jtl", rows)
    with src.open("rb") as f:
        rid = client.post("/api/aggregate-reports", files={"file": (src.name, f)}).json()["id"]
    meta = _wait(client, rid)
    assert meta["downloads"]["transactions_csv"] is None and meta["downloads"]["apis_csv"]
    r = client.get(f"/api/aggregate-reports/{rid}/transactions.csv")
    assert r.status_code == 404 and "no transactions" in r.json()["detail"]
    assert client.get(f"/api/aggregate-reports/{rid}/other.csv").status_code == 404


def test_interrupted_build_shows_as_failed(client, tmp_path):
    folder = tmp_path / "aggregate-reports" / "20260101-000000-abcdef"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text('{"id": "20260101-000000-abcdef", "title": "x", "status": "running", '
                                      '"created_at": "2026-01-01T00:00:00+00:00", "progress": []}')
    meta = client.get("/api/aggregate-reports/20260101-000000-abcdef").json()
    assert meta["status"] == "failed" and "Interrupted" in meta["error"]
