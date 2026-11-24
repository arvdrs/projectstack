"""
A run's samples as a JMeter CSV result file (JTL), from the results database: for runs whose
original file is gone, so JMeter can still build its HTML report for them.

Every sample comes back with its JTL values. The order is the one JMeter writes samples in
(as they end); the original file's own order is not stored, and JMeter's HTML report lists
the series of a graph partly in the order labels first appear, so with many labels the
colours of a graph's lines can differ from a report built from the original file. The
numbers do not.
"""

from __future__ import annotations

from pathlib import Path

# JTL column -> samples column (JMeter's default CSV columns, in its order)
JTL_COLUMNS = [
    ("timeStamp", "toUnixTimestamp64Milli(ts)"), ("elapsed", "elapsed"), ("label", "label"),
    ("responseCode", "response_code"), ("responseMessage", "response_message"), ("threadName", "thread_name"),
    ("dataType", "data_type"), ("success", "if(success, 'true', 'false')"), ("failureMessage", "failure_message"),
    ("bytes", "bytes"), ("sentBytes", "sent_bytes"), ("grpThreads", "grp_threads"), ("allThreads", "all_threads"),
    ("URL", "url"), ("Latency", "latency"), ("IdleTime", "idle_time"), ("Connect", "connect"),
]


def export_jtl(db, run_id: str, path: Path) -> int:
    """Write the run's samples to `path` as a CSV JTL with a header row; returns the number of bytes."""
    select = ", ".join(expr for _, expr in JTL_COLUMNS)
    sql = (f"SELECT {select} FROM {db.db}.samples WHERE run_id = {{run:String}} "
           "ORDER BY ts + toIntervalMillisecond(elapsed), ts, thread_name FORMAT CSV")
    with path.open("wb") as out:
        header = (",".join(c for c, _ in JTL_COLUMNS) + "\n").encode()
        out.write(header)
        return len(header) + db.stream_to(sql, {"run": run_id}, out)
