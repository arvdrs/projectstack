"""
JMeter's HTML dashboard from an existing result file, as `jmeter -g results.jtl -o report/`.

JMeter's report generator reads only CSV with a header row it understands. Files it
cannot read as they are (XML results, gzipped files, CSV without a header row or with
another delimiter) are first rewritten as a plain comma-separated CSV with a header
row. JMeter computes every number in the report; nothing here changes the samples.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from projectstack.results.columns import canonical
from projectstack.results.ingest import XML_COLUMNS, IngestError, open_jtl, sniff, xml_rows
from projectstack.runner.jmeter_runner import find_jmeter

# Over-time graphs: the smallest of these steps that keeps a graph at or under
# MAX_POINTS points. JMeter needs at least 1000 ms; its own default is 60 000 ms.
GRANULARITY_STEPS_MS = (2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 1_800_000)
MAX_POINTS = 200
JMETER_DEFAULT_GRANULARITY_MS = 60_000          # what JMeter uses when the report does not set one
TIMEOUT_S = 3 * 3600

# Columns of the XML conversion that JMeter's CSV header parser does not accept:
# psKind is ProjectStack's own, and JMeter rejects Hostname in a header row.
_NOT_IN_JMETER_HEADER = {"psKind", "Hostname"}

Log = Callable[[str], None]


class ReportError(ValueError):
    """The report cannot be generated; the message says why, for the user."""


@dataclass
class ReportOptions:
    title: str = ""
    apdex_satisfied_ms: int | None = None      # JMeter default 500
    apdex_tolerated_ms: int | None = None      # JMeter default 1500
    granularity_ms: int | None = None          # None: JMeter's own default (1 minute), as `jmeter -g` does
    auto_granularity: bool = False             # instead, chosen from the test's length (about 200 points)
    label_filter: str = ""                     # Java regex of labels to keep; empty keeps all

    def jmeter_properties(self) -> dict[str, str]:
        props: dict[str, str] = {}
        if self.title:
            props["jmeter.reportgenerator.report_title"] = self.title     # JMeter escapes it in the HTML
        if self.apdex_satisfied_ms is not None:
            props["jmeter.reportgenerator.apdex_satisfied_threshold"] = str(self.apdex_satisfied_ms)
        if self.apdex_tolerated_ms is not None:
            props["jmeter.reportgenerator.apdex_tolerated_threshold"] = str(self.apdex_tolerated_ms)
        if self.granularity_ms:
            props["jmeter.reportgenerator.overall_granularity"] = str(self.granularity_ms)
        if self.label_filter:
            props["jmeter.reportgenerator.sample_filter"] = self.label_filter
        return props


# ---------------------------------------------------------------------------
# Input: a CSV JMeter can read
# ---------------------------------------------------------------------------

def prepare_input(path: Path, work_dir: Path, log: Log = print) -> Path:
    """
    The file to give JMeter: `path` itself when JMeter can read it as it is,
    otherwise a rewritten CSV in `work_dir`. Raises ReportError for files that
    are not JMeter results.
    """
    try:
        info = sniff(path)
    except IngestError as exc:
        raise ReportError(str(exc)) from exc
    # JMeter shows the file name on the dashboard ("Source file"), so a rewritten file keeps the upload's name
    out = work_dir / f"{path.name.split('.')[0] or 'results'}.csv"
    if out.resolve() == path.resolve():
        out = out.with_name(f"{out.stem}-for-jmeter.csv")      # never write over the file being read

    if info.fmt == "xml":
        log("XML results: converting to CSV, which JMeter's report generator needs")
        keep = [i for i, c in enumerate(XML_COLUMNS) if c not in _NOT_IN_JMETER_HEADER]
        n = 0
        with out.open("w", newline="", encoding="utf-8") as dst:
            w = csv.writer(dst, lineterminator="\n")
            w.writerow([XML_COLUMNS[i] for i in keep])
            for row in xml_rows(path):
                w.writerow([row[i] for i in keep])
                n += 1
        if n == 0:
            raise ReportError(f"{path.name}: no samples found in the XML file")
        log(f"Converted {n:,} samples")
        return out

    header = [canonical(c) or c for c in info.columns]
    if info.delimiter != ",":
        name = {"\t": "tab", ";": "semicolon", "|": "pipe"}.get(info.delimiter, repr(info.delimiter))
        log(f"Columns are separated by {name}s: rewriting with commas for JMeter")
        with open_jtl(path) as src, out.open("w", newline="", encoding="utf-8") as dst:
            rows = csv.reader(src, delimiter=info.delimiter)
            if info.has_header_row:
                next(rows, None)
            w = csv.writer(dst, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
        return out

    if info.gzipped or not info.has_header_row or header != info.columns:
        if info.gzipped:
            log("Decompressing the gzipped file")
        if not info.has_header_row:
            log("No header row: adding one with JMeter's default columns")
        opener = gzip.open if info.gzipped else open
        with opener(path, "rb") as src, out.open("wb") as dst:
            if info.has_header_row:
                src.readline()
            dst.write((",".join(header) + "\n").encode("utf-8"))
            shutil.copyfileobj(src, dst, 1 << 20)
        return out

    return path


def time_span_ms(csv_path: Path) -> int | None:
    """
    Roughly how long the test ran, from the timestamps at the start and end of a CSV
    with a header row. Rows are not strictly in time order, so it uses the earliest
    and latest of the first and last 64 KB. None if it cannot tell.
    """
    with csv_path.open("rb") as f:
        head = f.read(1 << 16)
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - (1 << 16)))
        tail = f.read()
    lines = head.decode("utf-8", "replace").splitlines()
    if len(lines) < 2:
        return None
    try:
        idx = [canonical(c) for c in next(csv.reader([lines[0]]))].index("timeStamp")
    except ValueError:
        return None
    body = lines[1:-1] if size > len(head) else lines[1:]          # the last head line may be cut off
    body += tail.decode("utf-8", "replace").splitlines()[1:]        # the first tail line may be cut off
    stamps = []
    for row in csv.reader(body):
        if len(row) > idx and row[idx].strip().isdigit():
            stamps.append(int(row[idx]))
    return max(stamps) - min(stamps) if len(stamps) >= 2 else None


def auto_granularity_ms(span_ms: int | None) -> int:
    if not span_ms:
        return GRANULARITY_STEPS_MS[0]
    return next((g for g in GRANULARITY_STEPS_MS if span_ms / g <= MAX_POINTS), GRANULARITY_STEPS_MS[-1])


def fmt_granularity(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60} min" if s >= 60 and s % 60 == 0 else f"{s} s"


# ---------------------------------------------------------------------------
# JMeter
# ---------------------------------------------------------------------------

def run_report_generator(csv_path: Path, out_dir: Path, work_dir: Path, options: ReportOptions,
                         timeout: int = TIMEOUT_S) -> None:
    """Run `jmeter -g csv -o out_dir`; raises ReportError with JMeter's reason when it fails."""
    jmeter = find_jmeter()
    if not jmeter:
        raise ReportError("JMeter was not found. Set its location in Settings → JMeter path.")
    if out_dir.exists():
        shutil.rmtree(out_dir)                     # JMeter refuses a non-empty output folder
    log_path = work_dir / "jmeter.log"
    cmd = [jmeter, "-g", str(csv_path), "-o", str(out_dir), "-j", str(log_path),
           f"-Jjmeter.reportgenerator.temp_dir={work_dir / 'report-temp'}"]
    cmd += [f"-J{k}={v}" for k, v in options.jmeter_properties().items()]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=work_dir)
    except subprocess.TimeoutExpired as exc:
        raise ReportError(f"JMeter did not finish the report within {timeout // 60} minutes") from exc
    finally:
        shutil.rmtree(work_dir / "report-temp", ignore_errors=True)
    if proc.returncode != 0 or not (out_dir / "index.html").is_file():
        raise ReportError(jmeter_error(proc.stdout + "\n" + proc.stderr, log_path, options))


def jmeter_error(output: str, log_path: Path, options: ReportOptions) -> str:
    """JMeter's reason for failing, without its console noise and repeated wrappers."""
    lines = [ln.strip() for ln in output.splitlines()
             if ln.strip() and "StatusConsoleListener" not in ln and "does not contain the field names header" not in ln]
    message = next((ln for ln in lines if ln.startswith("An error occurred")), "")
    if message:
        # "An error occurred: Error while processing samples: Consumer failed with message :… :<reason>"
        reason = message.split("message :")[-1].split("An error occurred:")[-1].strip()
        detail = lines[lines.index(message) + 1:lines.index(message) + 3]   # a regex error points at the spot
        if reason in ("", "null") and options.label_filter:
            return f"No samples have a label matching the filter {options.label_filter!r}."
        if reason in ("", "null"):
            reason = "no reason given"
        if "near index" in reason and options.label_filter:
            return f"The label filter is not a valid regular expression: {reason}"
        return "JMeter could not generate the report: " + " ".join([reason, *detail]).strip()
    log_errors = []
    try:
        log_errors = [ln.split(" ERROR ", 1)[1].strip() for ln in log_path.read_text(errors="replace").splitlines()
                      if " ERROR " in ln]
    except OSError:
        pass
    tail = " ".join((log_errors or lines)[-3:]).strip()
    return "JMeter could not generate the report" + (f": {tail}" if tail else ".")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def report_summary(out_dir: Path) -> dict[str, Any]:
    """Headline numbers of a generated dashboard, from its statistics.json and index.html."""
    stats: dict[str, dict] = json.loads((out_dir / "statistics.json").read_text())
    total = stats.pop("Total", {}) or {}
    index = (out_dir / "index.html").read_text(errors="replace")

    def info(label: str) -> str | None:
        m = re.search(rf"<td>{label}</td>\s*<td>\"?([^<\"]*)\"?</td>", index)
        return m.group(1).strip() if m else None

    def num(key: str, digits: int = 1) -> float | None:
        v = total.get(key)
        return round(float(v), digits) if isinstance(v, (int, float)) else None

    return {
        "samples": int(total.get("sampleCount") or 0),
        "errors": int(total.get("errorCount") or 0),
        "error_pct": num("errorPct", 2),
        "mean_ms": num("meanResTime"),
        "median_ms": num("medianResTime"),
        "p90_ms": num("pct1ResTime"),
        "p95_ms": num("pct2ResTime"),
        "p99_ms": num("pct3ResTime"),
        "max_ms": num("maxResTime"),
        "throughput": num("throughput", 2),
        "labels": len(stats),
        "start_time": info("Start Time"),
        "end_time": info("End Time"),
    }


def generate_html_report(jtl: Path, out_dir: Path, work_dir: Path, options: ReportOptions | None = None,
                         log: Log = print) -> dict[str, Any]:
    """
    The whole job: prepare the input, pick the granularity, run JMeter, summarise.
    `work_dir` holds intermediate files (a rewritten CSV, JMeter's log); the caller
    removes it. Returns the summary and the options used.
    """
    options = options or ReportOptions()
    work_dir.mkdir(parents=True, exist_ok=True)
    csv_path = prepare_input(jtl, work_dir, log)
    if options.auto_granularity and not options.granularity_ms:
        span = time_span_ms(csv_path)
        options = replace(options, granularity_ms=auto_granularity_ms(span), auto_granularity=False)
        length = f" for a test of about {max(1, round(span / 60_000))} min" if span else ""
        log(f"Graph granularity: {fmt_granularity(options.granularity_ms)}{length}")
    log("Generating the dashboard with JMeter …")
    run_report_generator(csv_path, out_dir, work_dir, options)
    summary = report_summary(out_dir)
    return {"summary": summary, "options": options}
