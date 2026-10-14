"""
JTL ingest: JMeter result files (CSV with or without a header row, or XML,
optionally gzipped) into the ClickHouse `samples` table.

CSV is streamed to ClickHouse untouched and parsed there by
INSERT … SELECT … FROM input(…), which also converts timestamps and marks
Transaction Controller parents. XML is parsed here with a streaming parser and
converted to CSV on the fly, so both formats take the same path into the table.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from projectstack.results.columns import (
    DEFAULT_HEADER, DEFAULTS, KNOWN_COLUMNS, REQUIRED_COLUMNS, canonical, header_from_properties, read_properties,
)
from projectstack.results.db import ResultsDB, file_chunks, quote_literal

TC_MESSAGE = "Number of samples in transaction"
_SUB_RE = re.compile(r"^(.+)-(\d+)$")


class IngestError(ValueError):
    """The result file cannot be read; the message says why."""


@dataclass
class FileInfo:
    path: Path
    fmt: str                       # csv | xml
    gzipped: bool
    columns: list[str]             # file columns in order (header row, properties or JMeter defaults)
    has_header_row: bool = True
    delimiter: str = ","
    column_source: str = "header"  # header | properties | jmeter-defaults | xml

    @property
    def present(self) -> list[str]:
        """Known JMeter columns the file has, in canonical spelling."""
        return [c for c in (canonical(n) for n in self.columns) if c]


@dataclass
class IngestResult:
    rows: int
    seconds: float
    info: FileInfo
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _open_text(path: Path) -> io.TextIOBase:
    with open(path, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    raw = gzip.open(path, "rb") if gz else open(path, "rb")
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")


def _is_gzip(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(2) == b"\x1f\x8b"


def sniff(path: str | Path, props_path: str | Path | None = None) -> FileInfo:
    """Work out the format, columns and delimiter of a JTL file and check its timestamps."""
    path = Path(path)
    gz = _is_gzip(path)
    with _open_text(path) as f:
        head = f.read(1 << 20)
    if not head.strip():
        raise IngestError(f"{path.name} is empty")
    if head.lstrip().startswith("<"):
        return FileInfo(path, "xml", gz, [], has_header_row=False, column_source="xml")

    first_line = head.splitlines()[0]
    for delim in (",", "\t", ";", "|"):
        tokens = [t.strip().strip('"') for t in first_line.split(delim)]
        names = {canonical(t) for t in tokens}
        if {"timeStamp", "elapsed"} <= names or {"timeStamp", "label"} <= names:
            info = FileInfo(path, "csv", gz, tokens, True, delim, "header")
            break
    else:
        if props_path:
            cols, delim = header_from_properties(read_properties(props_path))
            info = FileInfo(path, "csv", gz, cols, False, delim, "properties")
        else:
            info = FileInfo(path, "csv", gz, list(DEFAULT_HEADER), False, ",", "jmeter-defaults")

    missing = [c for c in REQUIRED_COLUMNS if c not in info.present]
    if missing:
        raise IngestError(f"{path.name} has no {', '.join(missing)} column — is it a JMeter result file?")
    _check_timestamps(info, head)
    return info


def _check_timestamps(info: FileInfo, head: str) -> None:
    lines = head.splitlines(keepends=True)
    body = "".join(lines[1:] if info.has_header_row else lines)
    reader = csv.reader(io.StringIO(body), delimiter=info.delimiter)
    try:
        row = next(reader)
    except StopIteration:
        raise IngestError(f"{info.path.name} has a header but no samples")
    idx = [canonical(c) for c in info.columns].index("timeStamp")
    if len(row) <= idx:
        raise IngestError(
            f"{info.path.name}: the first sample has {len(row)} fields but timeStamp is column {idx + 1}"
            + (" — pass --props with the user.properties the test ran with" if not info.has_header_row else "")
        )
    value = row[idx].strip()
    if not value.isdigit():
        raise IngestError(
            f"{info.path.name}: timeStamp {value!r} is not epoch milliseconds. Re-run with "
            "jmeter.save.saveservice.timestamp_format=ms (the JMeter default)."
        )
    if len(value) < 12:
        raise IngestError(f"{info.path.name}: timeStamp {value} looks like seconds; JTL timestamps must be milliseconds")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest(db: ResultsDB, run_id: str, info: FileInfo, max_bad_rows: int = 0) -> IngestResult:
    """Load the file into `samples` under run_id."""
    started = time.monotonic()
    warnings: list[str] = []
    if info.fmt == "xml":
        info, chunks, gz = _xml_as_csv(info, warnings)
        if not db.cfg.is_local:
            chunks, gz = _gzip_stream(chunks), True
    else:
        chunks, gz = _csv_chunks(db, info)

    columns = info.columns
    present = {}
    for n in columns:
        c = canonical(n)
        if c and c not in present:
            present[c] = n
    if info.has_header_row:
        fmt = "CSVWithNames"
        structure = [(present[c], KNOWN_COLUMNS[c][0]) for c in present]
        if "psKind" in columns:
            structure.append(("psKind", "String"))
    else:
        # Every column must be listed, in file order; unknown or repeated ones are read and dropped
        fmt = "CSV"
        structure, used = [], set()
        for i, n in enumerate(columns):
            c = canonical(n)
            if c and c not in used:
                used.add(c)
                structure.append((n, KNOWN_COLUMNS[c][0]))
            else:
                structure.append((f"_extra_{i}", "String"))

    exprs = {"run_id": quote_literal(run_id)}
    for c, name in present.items():
        _, expr, target = KNOWN_COLUMNS[c]
        exprs[target] = expr.format(c=_ident(name))
    for target, default in DEFAULTS.items():
        exprs.setdefault(target, default)
    if "psKind" in columns:
        exprs["kind"] = "psKind"
    elif "responseMessage" in present:
        exprs["kind"] = f"if(startsWith({_ident(present['responseMessage'])}, {quote_literal(TC_MESSAGE)}), 'transaction', 'request')"
    else:
        exprs["kind"] = "'request'"

    targets = ["run_id", "ts", "elapsed", "latency", "connect", "idle_time", "label", "response_code",
               "response_message", "failure_message", "thread_name", "data_type", "success", "bytes",
               "sent_bytes", "grp_threads", "all_threads", "url", "hostname", "kind"]
    settings = {
        "input_format_with_names_use_header": 1,
        "input_format_skip_unknown_fields": 1,
        "input_format_csv_empty_as_default": 1,
        "input_format_allow_errors_num": max_bad_rows,
    }
    if info.delimiter == "\t":
        # ClickHouse's CSV parser refuses tab; CustomSeparated with CSV quoting reads it the same way
        fmt = fmt.replace("CSV", "CustomSeparated")
        settings.update({"format_custom_escaping_rule": "CSV", "format_custom_field_delimiter": "\t"})
    else:
        settings["format_csv_delimiter"] = info.delimiter
    struct_sql = ", ".join(f"{_ident(n)} {t}" for n, t in structure)
    sql = (f"INSERT INTO {db.db}.samples ({', '.join(targets)}) "
           f"SELECT {', '.join(exprs[t] for t in targets)} FROM input({quote_literal(struct_sql)}) FORMAT {fmt}")
    summary = db.stream_insert(sql, chunks, settings, gzip_body=gz)
    rows = int(summary.get("written_rows", 0) or 0)
    if "SampleCount" in present or "SampleCount" in columns:
        warnings.append("The file has a SampleCount column — rows may aggregate several samples; counts are per row")
    return IngestResult(rows, time.monotonic() - started, info, warnings)


def _ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _gzip_stream(chunks: Iterator[bytes]) -> Iterator[bytes]:
    import zlib
    comp = zlib.compressobj(1, zlib.DEFLATED, 31)
    for c in chunks:
        yield comp.compress(c)
    yield comp.flush()


def _csv_chunks(db: ResultsDB, info: FileInfo) -> tuple[Iterator[bytes], bool]:
    if info.gzipped:
        return file_chunks(info.path), True          # ClickHouse decompresses it
    if db.cfg.is_local:
        return file_chunks(info.path), False
    return file_chunks(info.path, gzip_level=1), True  # remote server: compress on the fly


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

# XML attribute -> JTL column
_XML_ATTRS = {
    "ts": "timeStamp", "t": "elapsed", "lb": "label", "rc": "responseCode", "rm": "responseMessage",
    "tn": "threadName", "dt": "dataType", "s": "success", "by": "bytes", "sby": "sentBytes",
    "ng": "grpThreads", "na": "allThreads", "lt": "Latency", "it": "IdleTime", "ct": "Connect", "hn": "Hostname",
}
_XML_COLUMNS = ["timeStamp", "elapsed", "label", "responseCode", "responseMessage", "threadName", "dataType",
                "success", "failureMessage", "bytes", "sentBytes", "grpThreads", "allThreads", "URL", "Latency",
                "IdleTime", "Connect", "Hostname", "psKind"]
_SKIP_TAGS = {"responseData", "samplerData", "requestHeader", "responseHeader", "cookies", "queryString",
              "method", "redirectLocation", "responseFile"}


def _xml_as_csv(info: FileInfo, warnings: list[str]) -> tuple[FileInfo, Iterator[bytes], bool]:
    """Stream an XML JTL as CSV rows (with a psKind column for the sample kind)."""
    seen_attrs: set[str] = set()

    def rows() -> Iterator[list[str]]:
        stack: list[dict] = []
        root = None
        assertion: dict | None = None
        with _open_text(info.path) as f:
            for event, elem in ET.iterparse(f, events=("start", "end")):
                if root is None:
                    root = elem
                is_sample = "ts" in elem.attrib and "lb" in elem.attrib
                if event == "start":
                    if is_sample:
                        parent = stack[-1] if stack else None
                        rm = elem.get("rm", "")
                        if rm.startswith(TC_MESSAGE):
                            kind = "transaction"
                        elif parent is None or parent["kind"] == "transaction":
                            kind = "request"      # top level, or a child of a parent-sample Transaction Controller
                        else:
                            kind = "subresult"    # redirect / embedded resource of a request
                        stack.append({"a": dict(elem.attrib), "kind": kind, "url": "", "failures": []})
                    elif elem.tag == "assertionResult":
                        assertion = {"failure": False, "message": ""}
                    continue
                tag = elem.tag
                if tag == "java.net.URL" and stack:
                    stack[-1]["url"] = (elem.text or "").strip()
                elif assertion is not None and tag in ("failure", "error"):
                    assertion["failure"] = assertion["failure"] or (elem.text or "").strip() == "true"
                elif assertion is not None and tag == "failureMessage":
                    assertion["message"] = (elem.text or "").strip()
                elif tag == "assertionResult" and assertion is not None:
                    if assertion["failure"] and stack:
                        stack[-1]["failures"].append(assertion["message"])
                    assertion = None
                if is_sample and stack:
                    s = stack.pop()
                    a = s["a"]
                    seen_attrs.update(a)
                    yield [
                        a.get("ts", ""), a.get("t", "0"), a.get("lb", ""), a.get("rc", ""), a.get("rm", ""),
                        a.get("tn", ""), a.get("dt", ""), a.get("s", "true"), s["failures"][0] if s["failures"] else "",
                        a.get("by", "0"), a.get("sby", "0"), a.get("ng", "0"), a.get("na", "0"), s["url"],
                        a.get("lt", "0"), a.get("it", "0"), a.get("ct", "0"), a.get("hn", ""), s["kind"],
                    ]
                    elem.clear()
                    if not stack and root is not None:
                        root.clear()          # keep memory flat on multi-GB files
                elif tag in _SKIP_TAGS:
                    elem.clear()

    def chunks() -> Iterator[bytes]:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(_XML_COLUMNS)
        n = 0
        for row in rows():
            w.writerow(row)
            n += 1
            if buf.tell() > (2 << 20):
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate()
        if n == 0:
            raise IngestError(f"{info.path.name}: no samples found in the XML file")
        yield buf.getvalue().encode("utf-8")

    xml_info = FileInfo(info.path, "xml", info.gzipped, list(_XML_COLUMNS), True, ",", "xml")
    xml_info._seen_attrs = seen_attrs  # type: ignore[attr-defined]  # filled while streaming
    return xml_info, chunks(), False


def present_columns(info: FileInfo) -> list[str]:
    """Known columns the file really had (for XML: attributes that occurred)."""
    if info.fmt == "xml":
        seen = getattr(info, "_seen_attrs", set())
        cols = [col for attr, col in _XML_ATTRS.items() if attr in seen]
        return cols + ["URL", "failureMessage"]
    return info.present


# ---------------------------------------------------------------------------
# Label classification
# ---------------------------------------------------------------------------

def classify_labels(db: ResultsDB, run_id: str) -> dict[str, str]:
    """
    Decide each label's kind once and store it in run_labels:
      transaction — Transaction Controller parent samples
      subresult   — redirect / embedded-resource children ("Login-0" next to "Login"),
                    whose samples fall inside a sample of the base label on the same thread
      request     — everything else
    """
    rows = db.rows(
        f"SELECT label, countIf(kind = 'transaction'), countIf(kind = 'subresult'), count(), min(ts) "
        f"FROM {db.db}.samples WHERE run_id = {{run:String}} GROUP BY label",
        {"run": run_id},
    )
    labels = {r[0] for r in rows}
    kinds: dict[str, str] = {}
    for label, tx, sub, n, _ in rows:
        if tx:
            kinds[label] = "transaction"
        elif sub:
            kinds[label] = "subresult"
        else:
            kinds[label] = "request"
    for label, tx, sub, n, _ in rows:
        m = _SUB_RE.match(label)
        if kinds[label] != "request" or not m or m.group(1) not in labels or kinds.get(m.group(1)) == "transaction":
            continue
        inside = db.rows(
            f"""SELECT countIf(c.ts <= b.ts + toIntervalMillisecond(b.elapsed)) / count()
                FROM (SELECT thread_name, ts FROM {db.db}.samples WHERE run_id = {{run:String}} AND label = {{c:String}}) AS c
                ASOF LEFT JOIN (SELECT thread_name, ts, elapsed FROM {db.db}.samples
                                WHERE run_id = {{run:String}} AND label = {{b:String}}) AS b
                ON c.thread_name = b.thread_name AND c.ts >= b.ts""",
            {"run": run_id, "c": label, "b": m.group(1)},
        )[0][0]
        if inside >= 0.9:
            kinds[label] = "subresult"
    db.insert("run_labels", [[run_id, label, kinds[label], n, first] for label, _, _, n, first in rows],
              ["run_id", "label", "kind", "samples", "first_ts"])
    return kinds
