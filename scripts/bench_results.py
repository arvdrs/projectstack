"""
Benchmark `pstack analyze` on a generated JTL.

    python scripts/bench_results.py --rows 1000000 [--keep]

Prints file size, time per stage, the Python process's peak memory and the
ClickHouse server's peak memory for this run's queries (from system.query_log).
The run is deleted afterwards unless --keep is given.
"""

from __future__ import annotations

import argparse
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from projectstack.config import load_settings  # noqa: E402
from projectstack.results import store  # noqa: E402
from projectstack.results.analyze import analyze_file  # noqa: E402
from projectstack.results.db import connect  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--labels", type=int, default=20)
    ap.add_argument("--file", type=Path, help="Reuse an existing JTL instead of generating one")
    ap.add_argument("--keep", action="store_true", help="Keep the run in ClickHouse")
    args = ap.parse_args()

    settings = load_settings()
    path = args.file
    if path is None:
        path = Path(tempfile.gettempdir()) / f"bench_{args.rows}.jtl"
        if not path.exists():
            # Generate in a separate process so its memory does not count towards analyze's peak
            import subprocess
            t = time.monotonic()
            subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); from tests.jtl_synth import bulk_csv; "
                            "bulk_csv(__import__('pathlib').Path(sys.argv[2]), rows=int(sys.argv[3]), labels=int(sys.argv[4]))",
                            str(Path(__file__).resolve().parent.parent), str(path), str(args.rows), str(args.labels)],
                           check=True)
            print(f"Generated {path} in {time.monotonic() - t:.1f}s")
    size_mb = path.stat().st_size / 1e6

    db = connect(settings)
    t = time.monotonic()
    result = analyze_file(path, settings=settings, tag="bench", db=db)
    wall = time.monotonic() - t
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    db.command("SYSTEM FLUSH LOGS")
    peak = db.rows(
        "SELECT max(memory_usage), argMax(substring(query, 1, 60), memory_usage) FROM system.query_log "
        "WHERE type = 'QueryFinish' AND event_time > now() - INTERVAL 1 HOUR AND position(query, {r:String}) > 0",
        {"r": result.run_id},
    )[0]

    rows = result.ingest.rows
    print(f"\nFile:        {path.name}  {size_mb:,.0f} MB  {rows:,} rows  {len(result.metrics['labels']['request'])} labels")
    print(f"Ingest:      {result.timings['ingest_s']:.2f}s  ({rows / result.timings['ingest_s']:,.0f} rows/s, "
          f"{size_mb / result.timings['ingest_s']:,.0f} MB/s)")
    print(f"Metrics:     {result.timings['metrics_s']:.2f}s (steady-state detection included)")
    print(f"Detectors:   {result.timings['detectors_s']:.2f}s  ({len(result.findings['findings'])} findings)")
    print(f"Total:       {wall:.2f}s")
    print(f"Peak memory: Python {rss_mb:,.0f} MB · ClickHouse {int(peak[0] or 0) / 1e6:,.0f} MB "
          f"(largest query: {peak[1]!r})")
    if not args.keep:
        store.delete_run(db, result.run_id, Path(settings["runs_dir"]))


if __name__ == "__main__":
    main()
