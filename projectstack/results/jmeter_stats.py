"""
The Statistics table of JMeter's HTML report (dashboard), computed from a run's samples.

The analysis works on the steady state with exact percentiles. Testers also read the
dashboard JMeter generates for the same file, so this module reproduces its Statistics
table rule for rule (JMeter 5.6, StatisticsSummaryConsumer), letting the two be read
side by side with the same numbers:

  * The whole run: every sample in the file, ramp-up and ramp-down included.
  * Empty transactions are left out: Transaction Controller samples whose message starts
    "Number of samples in transaction : 0" (the controller ran, nothing inside it did).
  * The Total row counts samples, not transactions: Transaction Controller samples
    (message "Number of samples in transaction …") are left out of it.
  * Average, min, max, error % and bytes cover every sample of the row.
  * The median and the 90th, 95th and 99th percentiles cover only the row's last 20,000
    samples (jmeter.reportgenerator.statistic_window) in the order JMeter wrote them, which
    is the order they ended. They are interpolated between neighbouring samples (Commons
    Math's default estimation, the same as ClickHouse's quantileExactExclusive).
  * Transactions/s and KB/s divide by the row's own span: first sample start to last
    sample end.

Samples never change after ingest, so the table is computed once per run and kept next
to findings.json. Its Total row is also kept in findings.json (`jmeter_total`): the
Performance Highlights take their request, error and throughput totals from it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from projectstack.results.db import ResultsDB

VERSION = 1
WINDOW = 20_000                         # jmeter.reportgenerator.statistic_window, JMeter's default
PERCENTILES = (0.5, 0.9, 0.95, 0.99)    # the median, then aggregate_rpt_pct1..3 (JMeter's defaults)
CACHE_FILE = "jmeter_statistics.json"
TC_MESSAGE = "Number of samples in transaction"
EMPTY_TC_MESSAGE = "Number of samples in transaction : 0"


def _query(db: ResultsDB, run_id: str, *, by_label: bool) -> list[dict[str, Any]]:
    """Per-label rows (by_label) or the single Total row, with their windowed percentiles."""
    p = {"run": run_id, "empty": EMPTY_TC_MESSAGE, "tc": TC_MESSAGE}
    scope = (f"FROM {db.db}.samples WHERE run_id = {{run:String}} "
             f"AND NOT startsWith(response_message, {{empty:String}})")
    if not by_label:
        scope += " AND NOT startsWith(response_message, {tc:String})"
    key, group = ("label", "GROUP BY label") if by_label else ("'Total' AS label", "")
    rows = db.dicts(
        f"""SELECT {key}, count() AS samples, countIf(NOT success) AS errors, avg(elapsed) AS avg_ms,
                   min(elapsed) AS min_ms, max(elapsed) AS max_ms, toUnixTimestamp64Milli(min(ts)) AS first_ms,
                   max(toUnixTimestamp64Milli(ts) + elapsed) AS end_ms, sum(bytes) AS bytes, sum(sent_bytes) AS sent
            {scope} {group}""", p)
    # The last WINDOW samples of each row, newest end first (JMeter writes a sample when it ends)
    last = (f"ORDER BY label, toUnixTimestamp64Milli(ts) + elapsed DESC LIMIT {int(WINDOW)} BY label" if by_label
            else f"ORDER BY toUnixTimestamp64Milli(ts) + elapsed DESC LIMIT {int(WINDOW)}")
    levels = ", ".join(map(str, PERCENTILES))
    q = {label: list(v) for label, v in db.rows(
        f"""SELECT label, quantilesExactExclusive({levels})(elapsed)
            FROM (SELECT {key}, elapsed {scope} {last}) GROUP BY label""", p)}
    return [_row(r, q.get(r["label"])) for r in rows]


def _row(r: dict[str, Any], q: list[float] | None) -> dict[str, Any]:
    n, errors = int(r["samples"]), int(r["errors"])
    span_s = (int(r["end_ms"]) - int(r["first_ms"])) / 1000 if n else 0.0
    q = q or [0.0] * len(PERCENTILES)

    def per_s(v: float) -> float:
        return round(v / span_s, 3) if span_s > 0 else 0.0

    return {
        "label": r["label"], "samples": n, "errors": errors,
        "error_pct": round(errors / n * 100, 3) if n else 0.0,
        "avg_ms": round(float(r["avg_ms"]), 2) if n else 0.0,
        "min_ms": int(r["min_ms"]) if n else 0, "max_ms": int(r["max_ms"]) if n else 0,
        "median_ms": round(q[0], 2), "p90_ms": round(q[1], 2), "p95_ms": round(q[2], 2), "p99_ms": round(q[3], 2),
        "tps": per_s(n), "received_kbps": per_s(int(r["bytes"]) / 1024), "sent_kbps": per_s(int(r["sent"]) / 1024),
    }


def compute(db: ResultsDB, run_id: str) -> dict[str, Any]:
    """The dashboard's Statistics table: a Total row and one row per label, sorted by label."""
    kinds = dict(db.rows(f"SELECT label, toString(kind) FROM {db.db}.run_labels FINAL WHERE run_id = {{r:String}}",
                         {"r": run_id}))
    labels = sorted(_query(db, run_id, by_label=True), key=lambda r: r["label"])
    if not labels:
        raise LookupError(f"No samples for run {run_id} in the results database (they may have expired)")
    return {
        "version": VERSION, "window": WINDOW, "percentiles": list(PERCENTILES[1:]),
        "total": _query(db, run_id, by_label=False)[0],
        "labels": [{**r, "kind": kinds.get(r["label"], "request")} for r in labels],
    }


def ensure_total(doc: dict[str, Any], folder: Path, db_factory: Callable[[], ResultsDB]) -> dict[str, Any]:
    """
    findings.json with the Total row (`jmeter_total`), which the highlights' totals come from.

    Runs analysed before it was kept there get it once, from the samples, and findings.json is
    saved with it. Without the database the document is returned unchanged (the highlights then
    fall back to their older totals).
    """
    run_id = (doc.get("run") or {}).get("run_id")
    if doc.get("jmeter_total") or not run_id:
        return doc
    try:
        total = load(db_factory(), run_id, folder)["total"]
    except Exception:                   # database down, samples expired: keep what there is
        return doc
    doc = {**doc, "jmeter_total": total}
    try:
        (folder / "findings.json").write_text(json.dumps(doc, indent=2, default=str))
    except OSError:
        pass
    return doc


def load(db: ResultsDB, run_id: str, folder: Path) -> dict[str, Any]:
    """The table from the run folder when computed before, otherwise computed now and kept there."""
    path = folder / CACHE_FILE
    try:
        doc = json.loads(path.read_text())
        if doc.get("version") == VERSION and doc.get("window") == WINDOW:
            return doc
    except (OSError, ValueError):
        pass
    doc = compute(db, run_id)
    try:
        path.write_text(json.dumps(doc))
    except OSError:
        pass                            # a read-only runs folder still gets the table
    return doc
