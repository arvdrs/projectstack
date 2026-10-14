"""
Run registry (ClickHouse `runs` table) and per-run artifact folders.

The data of a run lives in ClickHouse; its artifacts (meta.json, metrics.json,
and in later phases findings.json and reports) live in <runs_dir>/<run_id>/ so
CI can publish them and people can open them without a database client.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from projectstack.results.db import ResultsDB

_RUN_COLUMNS = ["run_id", "created_at", "scenario", "tag", "source_file", "source_format", "started_at", "ended_at",
                "samples", "peak_threads", "status", "verdict", "summary", "meta", "version"]


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def new_run_id(db: ResultsDB, tag: str = "") -> str:
    """YYYYMMDD-HHMMSS-<tag or random>, unique in the registry."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", tag).strip("-")[:24].lower() or secrets.token_hex(2)
    base = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{slug}"
    run_id, n = base, 1
    while db.rows(f"SELECT count() FROM {db.db}.runs WHERE run_id = {{r:String}}", {"r": run_id})[0][0]:
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def save_run(db: ResultsDB, run_id: str, *, status: str, meta: dict[str, Any], summary: dict[str, Any] | None = None,
             verdict: str = "unknown") -> None:
    """Insert a new version of the run's registry row."""
    db.insert("runs", [[
        run_id,
        datetime.fromisoformat(meta["created_at"]),
        meta.get("scenario", ""),
        meta.get("tag", ""),
        meta.get("source", {}).get("file", ""),
        meta.get("source", {}).get("format", ""),
        _utc(meta.get("run", {}).get("start_ms", 0)),
        _utc(meta.get("run", {}).get("end_ms", 0)),
        int(meta.get("run", {}).get("samples", 0)),
        int(meta.get("run", {}).get("peak_users", 0)),
        status,
        verdict,
        json.dumps(summary or {}),
        json.dumps(meta),
        time.time_ns(),
    ]], _RUN_COLUMNS)


def list_runs(db: ResultsDB, limit: int = 20, scenario: str | None = None) -> list[dict[str, Any]]:
    where = "status = 'ready'" + (" AND scenario = {s:String}" if scenario else "")
    rows = db.dicts(
        f"""SELECT run_id, created_at, scenario, tag, samples, peak_threads, verdict, summary,
                   toUnixTimestamp64Milli(started_at) AS start_ms, toUnixTimestamp64Milli(ended_at) AS end_ms
            FROM {db.db}.runs FINAL WHERE {where} ORDER BY created_at DESC LIMIT {int(limit)}""",
        {"s": scenario or ""},
    )
    for r in rows:
        r["summary"] = json.loads(r["summary"] or "{}")
    return rows


def resolve_run(db: ResultsDB, ref: str) -> str:
    """A full run id, from an id or a unique prefix of one."""
    rows = db.rows(f"SELECT run_id FROM {db.db}.runs FINAL WHERE status = 'ready' AND startsWith(run_id, {{p:String}}) "
                   "ORDER BY run_id", {"p": ref})
    ids = [r[0] for r in rows]
    if ref in ids:
        return ref
    if not ids:
        raise LookupError(f"No run matches {ref!r} — see: pstack runs list")
    if len(ids) > 1:
        raise LookupError(f"{ref!r} matches {len(ids)} runs: {', '.join(ids[:5])}{' …' if len(ids) > 5 else ''}")
    return ids[0]


def last_green(db: ResultsDB, scenario: str, before_run: str | None = None) -> str | None:
    """The latest run of a scenario whose verdict was pass (created before `before_run`, when given)."""
    before = (" AND created_at < (SELECT max(created_at) FROM {db}.runs FINAL WHERE run_id = {{b:String}})"
              .format(db=db.db) if before_run else "")
    rows = db.rows(f"SELECT run_id FROM {db.db}.runs FINAL WHERE status = 'ready' AND verdict = 'pass' "
                   f"AND scenario = {{s:String}}{before} ORDER BY created_at DESC LIMIT 1",
                   {"s": scenario, "b": before_run or ""})
    return rows[0][0] if rows else None


def get_meta(db: ResultsDB, run_id: str) -> dict[str, Any]:
    rows = db.rows(f"SELECT meta FROM {db.db}.runs FINAL WHERE run_id = {{r:String}}", {"r": run_id})
    if not rows:
        raise LookupError(f"Run {run_id} not found")
    return json.loads(rows[0][0])


def delete_run(db: ResultsDB, run_id: str, runs_dir: Path | None = None) -> None:
    for table in ("samples", "run_labels", "runs"):
        db.command(f"DELETE FROM {db.db}.{table} WHERE run_id = {{r:String}}", {"r": run_id})
    if runs_dir and (runs_dir / run_id).is_dir():
        shutil.rmtree(runs_dir / run_id)


def write_artifacts(runs_dir: Path, run_id: str, **docs: dict) -> Path:
    """Write each document as <name>.json in the run's folder; returns the folder."""
    folder = runs_dir / run_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, doc in docs.items():
        (folder / f"{name}.json").write_text(json.dumps(doc, indent=2, default=str))
    return folder


def read_artifact(runs_dir: Path, run_id: str, name: str) -> dict[str, Any] | None:
    path = runs_dir / run_id / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None
