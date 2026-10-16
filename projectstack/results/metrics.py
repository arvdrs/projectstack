"""
Run metrics, computed by ClickHouse over the raw samples.

Correctness rules this module follows:
  * Percentiles are exact (quantileExact) over every sample in scope — per label,
    per window, per time bucket — and never averaged across labels or buckets.
    quantileExact returns the value at 0-based position floor(N·p) of the sorted
    samples, the same definition as JMeter's Aggregate Report.
  * Transaction Controller parents, requests and redirect sub-results are kept
    apart (run_labels), so nothing is counted twice. Sub-results are excluded.
  * Throughput over the full run uses JMeter's definition: samples divided by the
    time from the first sample's start to the last sample's end.
"""

from __future__ import annotations

from typing import Any

from projectstack.results.db import ResultsDB
from projectstack.results.window import Window

PERCENTILES = (0.5, 0.9, 0.95, 0.99)
TOP_ERRORS = 15


def run_bounds(db: ResultsDB, run_id: str) -> dict[str, Any]:
    """First sample start, last sample start and end, sample counts and peak threads."""
    r = db.rows(
        f"""SELECT toUnixTimestamp64Milli(min(ts)), toUnixTimestamp64Milli(max(ts)),
                   max(toUnixTimestamp64Milli(ts) + elapsed), count(), max(all_threads),
                   countIf(kind = 'transaction'), countIf(kind = 'subresult')
            FROM {db.db}.samples WHERE run_id = {{run:String}}""",
        {"run": run_id},
    )[0]
    if not r[3]:
        raise ValueError("The file contained no samples")
    return {"start_ms": int(r[0]), "last_ts_ms": int(r[1]), "end_ms": int(r[2]), "samples": int(r[3]),
            "peak_all_threads": int(r[4]), "transaction_rows": int(r[5]), "subresult_rows": int(r[6])}


def _stats(row: dict, duration_s: float) -> dict[str, Any]:
    n = int(row["samples"])
    q = list(row["q"]) if n else [0] * len(PERCENTILES)
    errors = int(row["errors"])
    return {
        "samples": n,
        "avg_ms": round(float(row["avg_ms"]), 1) if n else 0.0,
        "min_ms": int(row["min_ms"]) if n else 0,
        "max_ms": int(row["max_ms"]) if n else 0,
        "p50_ms": int(q[0]), "p90_ms": int(q[1]), "p95_ms": int(q[2]), "p99_ms": int(q[3]),
        "errors": errors,
        "error_pct": round(errors / n * 100, 3) if n else 0.0,
        "tps": round(n / duration_s, 3) if duration_s > 0 else 0.0,
    }


_STAT_COLS = (
    "count() AS samples, avg(elapsed) AS avg_ms, min(elapsed) AS min_ms, max(elapsed) AS max_ms, "
    f"quantilesExact({', '.join(map(str, PERCENTILES))})(elapsed) AS q, countIf(NOT success) AS errors"
)


def in_kind(db: ResultsDB, *kinds: str) -> str:
    """SQL condition: the sample's label is of these kinds in the run.

    The kinds come from run_labels (written once at ingest) instead of being sent as a list:
    a test with thousands of labels would exceed ClickHouse's limit on a query parameter's size.
    """
    names = ", ".join(f"'{k}'" for k in kinds)
    return (f"label IN (SELECT label FROM {db.db}.run_labels FINAL "
            f"WHERE run_id = {{run:String}} AND kind IN ({names}))")


def compute(db: ResultsDB, run_id: str, window: Window, kinds: dict[str, str],
            bounds: dict[str, Any], users_from_threads: bool, bucket_s: int | None = None) -> dict[str, Any]:
    """Per-label stats and overall stats in the window, the timeline and top errors over the full run."""
    present = set(kinds.values())
    p = {"run": run_id, "ws": window.start_ms, "we": window.end_ms}
    in_window = ("run_id = {run:String} AND ts >= fromUnixTimestamp64Milli({ws:Int64}) "
                 "AND ts < fromUnixTimestamp64Milli({we:Int64})")

    labels = db.dicts(
        f"""SELECT label, {_STAT_COLS}, toUnixTimestamp64Milli(min(ts)) AS first_ms
            FROM {db.db}.samples
            WHERE {in_window} AND {in_kind(db, "request", "transaction")}
            GROUP BY label ORDER BY first_ms""",
        p,
    )
    per_label = {"request": [], "transaction": []}
    for row in labels:
        per_label[kinds[row["label"]]].append({"label": row["label"], **_stats(row, window.duration_s)})

    overall = {}
    for kind in ("request", "transaction"):
        if kind not in present:
            continue
        row = db.dicts(f"SELECT {_STAT_COLS} FROM {db.db}.samples WHERE {in_window} AND {in_kind(db, kind)}", p)[0]
        overall[kind] = _stats(row, window.duration_s)

    return {
        "window": window.as_dict(),
        "overall": overall,
        "labels": per_label,
        "timeline": timeline(db, run_id, bounds, users_from_threads, bucket_s),
        "errors": top_errors(db, run_id),
    }


def timeline(db: ResultsDB, run_id: str, bounds: dict[str, Any],
             users_from_threads: bool, bucket_s: int | None = None) -> dict[str, Any]:
    """Per-bucket users, throughput, p95 and errors over the full run (requests only)."""
    run_s = (bounds["end_ms"] - bounds["start_ms"]) / 1000
    bucket = bucket_s or (60 if run_s >= 600 else 10)
    rows = db.rows(
        f"""SELECT toUnixTimestamp(toStartOfInterval(ts, toIntervalSecond({{b:UInt32}}))) * 1000 AS t,
                   count(), countIf(NOT success), quantileExact(0.95)(elapsed), avg(elapsed), avg(connect),
                   max(all_threads), uniqExact(thread_name)
            FROM {db.db}.samples WHERE run_id = {{run:String}} AND {in_kind(db, "request")}
            GROUP BY t ORDER BY t""",
        {"run": run_id, "b": bucket},
    )
    points = []
    for t, n, errors, p95, avg_ms, connect, all_threads, threads in rows:
        # Throughput over the part of the bucket the run actually covers
        covered = (min(t + bucket * 1000, bounds["end_ms"]) - max(t, bounds["start_ms"])) / 1000
        points.append({
            "t_ms": int(t),
            "users": int(threads if users_from_threads else all_threads),
            "samples": int(n),
            "tps": round(n / covered, 3) if covered > 0 else 0.0,
            "p95_ms": int(p95),
            "avg_ms": round(float(avg_ms), 1),
            "connect_ms": round(float(connect), 1),
            "errors": int(errors),
            "error_pct": round(errors / n * 100, 3) if n else 0.0,
        })
    return {"bucket_s": bucket, "users_source": "threadName" if users_from_threads else "allThreads",
            "points": points}


def top_errors(db: ResultsDB, run_id: str) -> list[dict[str, Any]]:
    """Most frequent failures over the full run, by label, response code and message."""
    rows = db.dicts(
        f"""SELECT label, response_code, response_message, substring(failure_message, 1, 200) AS failure,
                   count() AS count, toUnixTimestamp64Milli(min(ts)) AS first_ms,
                   toUnixTimestamp64Milli(max(ts)) AS last_ms
            FROM {db.db}.samples
            WHERE run_id = {{run:String}} AND NOT success AND {in_kind(db, "request")}
            GROUP BY label, response_code, response_message, failure ORDER BY count DESC LIMIT {TOP_ERRORS}""",
        {"run": run_id},
    )
    return [{**r, "count": int(r["count"]), "first_ms": int(r["first_ms"]), "last_ms": int(r["last_ms"])}
            for r in rows]
