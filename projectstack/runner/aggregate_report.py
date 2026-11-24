"""
JMeter's Aggregate Report for a result file (JTL), as two tables: the APIs (samplers) and
the transactions (Transaction Controller samples).

Every number follows JMeter 5.6's Aggregate Report listener: StatVisualizer, with one
SamplingStatCalculator per label and one for the TOTAL row. A table therefore shows what
that listener shows for the same samples:

  * Average: the mean elapsed time, cut to a whole millisecond (not rounded).
  * Median, 90%, 95% and 99% Line: the smallest elapsed time that at least round(n × p)
    of the row's n samples do not exceed, over all of its samples (no interpolation, no
    window). p is JMeter's float 0.9f, 0.95f and 0.99f, which changes the result for
    some n compared with exact 0.9, 0.95 and 0.99.
  * Error %: samples whose success is not "true", over all samples.
  * Throughput: samples per second between the first sample's start and the last
    sample's end (timeStamp + elapsed). Received and Sent KB/sec divide the bytes by the
    same span, with 1 KB = 1024 bytes.
  * Rows are in the order their label first appears in the file. TOTAL is last and
    covers every sample of its table.

How the samples are split between the two tables:

  * Transactions: labels with Transaction Controller samples (response message
    "Number of samples in transaction …").
  * APIs: every other label. Sub-results are left out unless asked for. JMeter writes the
    redirects and embedded resources of a request as rows of their own ("Login-0",
    "Login-1" after "Login"), and the parent row already includes their time. In a CSV
    file a label is a sub-result when it is "<label>-<n>", <label> is a request in the
    file, and at least 90% of its samples start inside a sample of <label> on the same
    thread. That is the rule the results analysis uses. XML files nest sub-results, so
    no guessing is needed there.
  * Empty transactions are left out unless asked for. These are samples with the message
    "Number of samples in transaction : 0": the controller ran, but none of its samplers
    did, and JMeter records 0 ms. JMeter's HTML report leaves them out; its Aggregate
    Report counts them.
  * Samples with a negative elapsed time are left out. JMeter writes one when a test
    stops in the middle of a Transaction Controller.

The CSV downloads are byte for byte what the listener's "Save Table Data" writes with
"Save Table Header" on. tests/jmeter_aggregate.groovy runs JMeter's own classes to check it.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter, deque
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from projectstack.results.columns import canonical
from projectstack.results.ingest import XML_COLUMNS, IngestError, open_jtl, sniff, xml_rows
from projectstack.results.jmeter_stats import EMPTY_TC_MESSAGE, TC_MESSAGE

# The percentile lines, as StatGraphVisualizer computes them: Float.parseFloat("90") / 100f
PERCENTILE_LINES = (90, 95, 99)                 # aggregate_rpt_pct1..3, JMeter's defaults
_P = tuple(float(np.float32(p) / np.float32(100)) for p in PERCENTILE_LINES)
COLUMNS = ["Label", "# Samples", "Average", "Median", *(f"{p}% Line" for p in PERCENTILE_LINES),
           "Min", "Max", "Error %", "Throughput", "Received KB/sec", "Sent KB/sec"]
TOTAL_LABEL = "TOTAL"
SUBRESULT_SHARE = 0.9                           # share of a label's samples inside its base label's samples
_SUB_RE = re.compile(r"^(.+)-(\d+)$")
_FAR_FUTURE = 1 << 62
PROGRESS_EVERY = 1_000_000

Log = Callable[[str], None]


class AggregateError(ValueError):
    """The report cannot be made; the message says why, for the user."""


@dataclass
class AggregateOptions:
    include_subresults: bool = False            # list redirect / embedded-resource sub-results as APIs
    include_empty_transactions: bool = False    # count "Number of samples in transaction : 0" samples


# ---------------------------------------------------------------------------
# One row: JMeter's SamplingStatCalculator
# ---------------------------------------------------------------------------

class _Calc:
    __slots__ = ("count", "sum", "values", "errors", "bytes", "sent", "first", "end")

    def __init__(self) -> None:
        self.count = 0                    # samples (SampleCount of each row, 1 without that column)
        self.sum = 0                      # elapsed ms
        self.values: Counter[int] = Counter()   # elapsed ms -> samples: StatCalculator's valuesMap
        self.errors = 0
        self.bytes = 0
        self.sent = 0
        self.first = _FAR_FUTURE          # earliest start
        self.end = 0                      # latest end

    def add(self, ts: int, elapsed: int, ok: bool, received: int, sent: int, sample_count: int = 1) -> None:
        self.count += sample_count
        self.sum += elapsed
        if sample_count > 1:              # an aggregated sample: n values of elapsed / n (Java long division)
            self.values[elapsed // sample_count] += sample_count
        else:
            self.values[elapsed] += 1
        if not ok:
            self.errors += 1
        self.bytes += received
        self.sent += sent
        if ts < self.first:
            self.first = ts
        if ts + elapsed > self.end:
            self.end = ts + elapsed

    def merge(self, other: _Calc) -> None:
        self.count += other.count
        self.sum += other.sum
        self.values.update(other.values)
        self.errors += other.errors
        self.bytes += other.bytes
        self.sent += other.sent
        self.first = min(self.first, other.first)
        self.end = max(self.end, other.end)

    def row(self, label: str) -> dict[str, Any]:
        n = self.count
        keys = sorted(self.values)

        def point(p: float) -> int:       # StatCalculator.getPercentPoint
            target = _java_round(n * p)
            for k in keys:
                target -= self.values[k]
                if target <= 0:
                    return k
            return 0

        span = self.end - self.first if self.end else 0
        throughput = (n / span) * 1000.0 if span > 0 else None          # None: JMeter's Infinity (one 0 ms sample)

        def per_s(total: int) -> float:  # SamplingStatCalculator.getRatePerSecond, then / 1024
            return (total / (span / 1000.0)) / 1024.0 if span > 0 and total > 0 else 0.0

        return {
            "label": label,
            "samples": n,
            "average": int(self.sum / n),                                # (long) mean: truncated
            "median": point(0.5),
            "p90": point(_P[0]), "p95": point(_P[1]), "p99": point(_P[2]),
            "min": keys[0], "max": keys[-1],
            "error_rate": self.errors / n,                               # a fraction: 0.0123 is 1.23%
            "throughput": throughput,                                    # per second
            "received_kbps": per_s(self.bytes),
            "sent_kbps": per_s(self.sent),
        }


def _java_round(x: float) -> int:
    """Math.round for x >= 0: the nearest whole number, halves up."""
    f = math.floor(x)
    return int(f) + (1 if x - f >= 0.5 else 0)


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

@dataclass
class _Sample:
    ts: int
    elapsed: int
    label: str
    message: str
    thread: str
    ok: bool
    received: int
    sent: int
    sample_count: int
    kind: str                     # XML only: transaction | request | subresult; "" for CSV


def _int(text: str) -> int:
    return int(text) if text else 0


def _csv_samples(path: Path, columns: list[str], has_header: bool, delimiter: str,
                 bad: list[int]) -> Iterator[_Sample]:
    names = [canonical(c) or c.strip() for c in columns]
    lower = [n.lower() for n in names]

    def idx(name: str) -> int | None:
        return lower.index(name.lower()) if name.lower() in lower else None

    i_ts, i_el, i_lb = idx("timeStamp"), idx("elapsed"), idx("label")
    i_rm, i_tn, i_ok = idx("responseMessage"), idx("threadName"), idx("success")
    i_by, i_sby, i_sc = idx("bytes"), idx("sentBytes"), idx("SampleCount")
    need = max(i for i in (i_ts, i_el, i_lb) if i is not None) + 1
    with open_jtl(path) as f:
        rows = csv.reader(f, delimiter=delimiter)
        if has_header:
            next(rows, None)
        for r in rows:
            if len(r) < need:
                if r:
                    bad[0] += 1           # a cut-off last line, usually
                continue
            try:
                yield _Sample(
                    int(r[i_ts]), int(r[i_el]), r[i_lb],
                    r[i_rm] if i_rm is not None and i_rm < len(r) else "",
                    r[i_tn] if i_tn is not None and i_tn < len(r) else "",
                    (r[i_ok] if i_ok is not None and i_ok < len(r) else "true").lower() == "true",
                    _int(r[i_by]) if i_by is not None and i_by < len(r) else 0,
                    _int(r[i_sby]) if i_sby is not None and i_sby < len(r) else 0,
                    max(1, _int(r[i_sc])) if i_sc is not None and i_sc < len(r) else 1,
                    "",
                )
            except ValueError:
                bad[0] += 1


def _xml_samples(path: Path, bad: list[int]) -> Iterator[_Sample]:
    col = {c: i for i, c in enumerate(XML_COLUMNS)}
    for r in xml_rows(path):
        try:
            yield _Sample(
                int(r[col["timeStamp"]]), int(r[col["elapsed"]]), r[col["label"]], r[col["responseMessage"]],
                r[col["threadName"]], r[col["success"]].lower() == "true", _int(r[col["bytes"]]),
                _int(r[col["sentBytes"]]), 1, r[col["psKind"]],
            )
        except ValueError:
            bad[0] += 1


# ---------------------------------------------------------------------------
# Sub-results in CSV files
# ---------------------------------------------------------------------------

class _SubresultFinder:
    """
    Which "<label>-<n>" labels of a CSV file are sub-results: at least SUBRESULT_SHARE of their
    samples start inside a sample of <label> on the same thread (the results analysis's rule).

    JMeter writes a request and then its sub-results. A file sorted afterwards can have them
    the other way round. So each thread keeps its last few samples, which may be a parent, and
    the "<label>-<n>" samples no parent was found for yet. A parent written after them still
    claims them.
    """
    RECENT = 8          # samples per thread that may be the parent of the next ones
    WAITING = 32        # "<label>-<n>" samples per thread still waiting for a parent

    def __init__(self) -> None:
        self.bases: dict[str, str] = {}               # label -> <label> of "<label>-<n>", "" for other labels
        self.counts: dict[str, list[int]] = {}        # "<label>-<n>" -> [samples inside a <label> sample, samples]
        self.recent: dict[str, deque] = {}            # thread -> (label, start, end)
        self.waiting: dict[str, deque] = {}           # thread -> (label, base, start)
        self.awaited: set[str] = set()                # bases some waiting sample needs

    def add(self, label: str, thread: str, start: int, end: int) -> None:
        base = self.bases.get(label)
        if base is None:
            m = _SUB_RE.match(label)
            base = self.bases[label] = m.group(1) if m else ""
        if base:
            counts = self.counts.get(label)
            if counts is None:
                counts = self.counts[label] = [0, 0]
            counts[1] += 1
            for lb, s, e in self.recent.get(thread, ()):
                if lb == base and s <= start <= e:
                    counts[0] += 1
                    return
            waiting = self.waiting.get(thread)
            if waiting is None:
                waiting = self.waiting[thread] = deque(maxlen=self.WAITING)
            waiting.append((label, base, start))
            self.awaited.add(base)
        recent = self.recent.get(thread)               # a request that may have sub-results of its own
        if recent is None:
            recent = self.recent[thread] = deque(maxlen=self.RECENT)
        recent.append((label, start, end))
        if label in self.awaited:
            waiting = self.waiting.get(thread)
            if waiting:
                left = [w for w in waiting if not (w[1] == label and start <= w[2] <= end)]
                if len(left) != len(waiting):
                    for w in waiting:
                        if w[1] == label and start <= w[2] <= end:
                            self.counts[w[0]][0] += 1
                    waiting.clear()
                    waiting.extend(left)

    def subresults(self, labels: dict[str, Any], transactions: set[str]) -> set[str]:
        """Sub-result labels: of a request label in the file, and not transactions themselves."""
        return {
            lb for lb, (inside, n) in self.counts.items()
            if lb not in transactions and self.bases[lb] in labels and self.bases[lb] not in transactions
            and inside >= SUBRESULT_SHARE * n
        }


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def build_aggregate(path: Path, options: AggregateOptions | None = None, log: Log = print) -> dict[str, Any]:
    """
    The two tables for a JTL (CSV with or without a header row, or XML; optionally gzipped):
    {"apis": {"rows", "total"}, "transactions": {"rows", "total"}, "notes", "summary"}.
    """
    options = options or AggregateOptions()
    try:
        info = sniff(path)
    except IngestError as exc:
        raise AggregateError(str(exc)) from exc
    bad = [0]
    samples = (_xml_samples(path, bad) if info.fmt == "xml"
               else _csv_samples(path, info.columns, info.has_header_row, info.delimiter, bad))

    calcs: dict[str, _Calc] = {}                  # label -> row, in order of first appearance
    transaction_labels: set[str] = set()
    xml_subresult_labels: set[str] = set()
    finder = _SubresultFinder()                   # CSV: XML files nest their sub-results
    empty: Counter[str] = Counter()               # empty transaction samples per label
    negative = 0
    read = 0

    for s in samples:
        read += 1
        if read % PROGRESS_EVERY == 0:
            log(f"Read {read:,} samples …")
        is_tc = s.message.startswith(TC_MESSAGE)
        if is_tc:
            transaction_labels.add(s.label)
            if not options.include_empty_transactions and s.message.startswith(EMPTY_TC_MESSAGE):
                empty[s.label] += 1
                continue
        elif s.kind == "subresult":
            xml_subresult_labels.add(s.label)
        if s.elapsed < 0:
            negative += 1
            continue
        if not s.kind:
            finder.add(s.label, s.thread, s.ts, s.ts + s.elapsed)
        calc = calcs.get(s.label)
        if calc is None:
            calc = calcs[s.label] = _Calc()
        calc.add(s.ts, s.elapsed, s.ok, s.received, s.sent, s.sample_count)

    if read == 0:
        raise AggregateError(f"{path.name}: no samples found")
    if not calcs:
        raise AggregateError(f"{path.name}: every sample was an empty transaction or had a negative elapsed time")

    if info.fmt == "xml":
        subresults = {lb for lb in xml_subresult_labels if lb not in transaction_labels}
    else:
        subresults = finder.subresults(calcs, transaction_labels)

    def table(labels: list[str]) -> dict[str, Any]:
        if not labels:
            return {"rows": [], "total": None}
        total = _Calc()
        for lb in labels:
            total.merge(calcs[lb])
        return {"rows": [calcs[lb].row(lb) for lb in labels], "total": total.row(TOTAL_LABEL)}

    tx = [lb for lb in calcs if lb in transaction_labels]
    apis = [lb for lb in calcs if lb not in transaction_labels and (options.include_subresults or lb not in subresults)]
    everything = _Calc()
    for c in calcs.values():
        everything.merge(c)
    sub_listed = [lb for lb in calcs if lb in subresults]
    return {
        "apis": table(apis),
        "transactions": table(tx),
        "notes": {
            "subresults": {"included": options.include_subresults, "labels": sub_listed,
                           "samples": sum(calcs[lb].count for lb in sub_listed)},
            "empty_transactions": {"included": options.include_empty_transactions,
                                   "samples": sum(empty.values()),
                                   "labels": [lb for lb in empty if lb in transaction_labels]},
            "negative_elapsed": negative,
            "unreadable_rows": bad[0],
        },
        "summary": {
            "format": info.fmt,
            "samples_read": read,
            "samples": everything.count,
            "labels": len(calcs),
            "start_ms": everything.first,
            "end_ms": everything.end,
        },
    }


# ---------------------------------------------------------------------------
# Output: JMeter's "Save Table Data" CSV, and the values its table shows
# ---------------------------------------------------------------------------

def _fixed(x: float, places: int) -> str:
    """java.text.DecimalFormat with `places` decimals: half-even on the double's exact value."""
    return f"{Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN):f}"


def _no_leading_zero(text: str) -> str:
    """The "#.0…" patterns have no required integer digit: 0.5 is written ".5"."""
    return text[1:] if text.startswith("0.") else text


def saved_values(row: dict[str, Any]) -> list[str]:
    """A row as "Save Table Data" writes it (StatGraphVisualizer.getFormatters)."""
    tp = row["throughput"]
    return [
        row["label"], str(row["samples"]), str(row["average"]), str(row["median"]),
        str(row["p90"]), str(row["p95"]), str(row["p99"]), str(row["min"]), str(row["max"]),
        _fixed(row["error_rate"] * 100, 3) + "%",                        # "#0.000%"
        "∞" if tp is None else _no_leading_zero(_fixed(tp, 5)),          # "#.00000"
        _fixed(row["received_kbps"], 2), _fixed(row["sent_kbps"], 2),      # "#0.00"
    ]


def shown_values(row: dict[str, Any]) -> dict[str, str]:
    """Error %, Throughput and KB/sec as the listener's table shows them (its cell renderers)."""
    rate, unit = row["throughput"], "sec"
    if rate is None:
        throughput = "∞/sec"
    else:
        if rate < 1.0:                                                    # RateRenderer
            rate, unit = rate * 60.0, "min"
        if rate < 1.0:
            rate, unit = rate * 60.0, "hour"
        throughput = f"{_no_leading_zero(_fixed(rate, 1))}/{unit}"      # "#.0"
    return {
        "error": _fixed(row["error_rate"] * 100, 2) + "%",               # "#0.00%"
        "throughput": throughput,
        "received": _fixed(row["received_kbps"], 2),
        "sent": _fixed(row["sent_kbps"], 2),
    }


def _quote(text: str) -> str:
    """CSVSaveService.quoteDelimiters with the delimiter and the quote character."""
    return f'"{text.replace(chr(34), chr(34) * 2)}"' if ("," in text or '"' in text) else text


def table_csv(table: dict[str, Any]) -> str:
    """One table as JMeter's Aggregate Report saves it: header, rows, TOTAL."""
    lines = [",".join(_quote(h) for h in COLUMNS)]
    rows = [*table["rows"], *([table["total"]] if table.get("total") else [])]
    lines += [",".join(_quote(v) for v in saved_values(r)) for r in rows]
    return "\n".join(lines) + "\n"


XLSX_FORMATS = [None, "#,##0", "#,##0", "#,##0", "#,##0", "#,##0", "#,##0", "#,##0", "#,##0",
                "0.00%", "0.00", "0.00", "0.00"]


def table_cells(table: dict[str, Any]) -> list[list[Any]]:
    """One table's rows as spreadsheet cells (numbers kept as numbers), TOTAL last."""
    rows = [*table["rows"], *([table["total"]] if table.get("total") else [])]
    return [[r["label"], r["samples"], r["average"], r["median"], r["p90"], r["p95"], r["p99"], r["min"], r["max"],
             r["error_rate"], "∞" if r["throughput"] is None else r["throughput"], r["received_kbps"], r["sent_kbps"]]
            for r in rows]
