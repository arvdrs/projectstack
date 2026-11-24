"""
JMeter Aggregate Reports in the web app: upload a result file (JTL) and get JMeter's
Aggregate Report for it as two tables, the APIs and the transactions, each downloadable as
the CSV JMeter's "Save Table Data" writes, and both together as an Excel workbook. Part of
Reports, next to the HTML report; it needs neither JMeter nor ClickHouse.

Each report is a folder under REPORTS_DIR:
    <id>/meta.json      title, source file, options, status, progress, summary, notes
    <id>/tables.json    the two tables (runner/aggregate_report.py)
The uploaded file is deleted once the tables are built.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from projectstack.results.ingest import IngestError, sniff
from projectstack.results.xlsx import Sheet, workbook
from projectstack.runner.aggregate_report import (
    COLUMNS, XLSX_FORMATS, AggregateError, AggregateOptions, build_aggregate, shown_values, table_cells, table_csv,
)

router = APIRouter(prefix="/api/aggregate-reports", tags=["aggregate-reports"])

REPORTS_DIR = Path.home() / ".projectstack" / "aggregate-reports"
TABLES = {"apis": "APIs", "transactions": "Transactions"}

_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_active: dict[str, dict[str, Any]] = {}       # id -> meta of reports queued or being built
_lock = threading.Lock()
_slot = threading.Semaphore(1)                # one file read at a time: it is CPU-bound


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _folder(report_id: str) -> Path:
    if not _ID_RE.match(report_id):
        raise HTTPException(status_code=404, detail="Unknown report")
    return REPORTS_DIR / report_id


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def _read_meta(report_id: str) -> dict[str, Any] | None:
    with _lock:
        if report_id in _active:
            return json.loads(json.dumps(_active[report_id]))      # a copy: the job keeps writing to it
    try:
        meta = json.loads((REPORTS_DIR / report_id / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    if meta.get("status") in ("queued", "running"):
        meta["status"] = "failed"
        meta["error"] = "Interrupted: ProjectStack stopped while this report was being built. Upload the file again."
    return meta


def _tables(report_id: str) -> dict[str, Any]:
    meta = _read_meta(report_id)
    if not meta or meta.get("status") != "done":
        raise HTTPException(status_code=404, detail="This report is not available")
    try:
        return json.loads((REPORTS_DIR / report_id / "tables.json").read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="This report's tables are missing") from None


def _public(meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta)
    done = meta.get("status") == "done"
    base = f"/api/aggregate-reports/{meta['id']}"
    counts = meta.get("counts") or {}
    out["downloads"] = {
        "apis_csv": f"{base}/apis.csv" if done and counts.get("apis") else None,
        "transactions_csv": f"{base}/transactions.csv" if done and counts.get("transactions") else None,
        "xlsx": f"{base}/aggregate.xlsx" if done else None,
    } if done else None
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")[:80] or "aggregate-report"


# ---------------------------------------------------------------------------
# Background build
# ---------------------------------------------------------------------------

def _start(meta: dict[str, Any], upload: Path, options: AggregateOptions) -> None:
    rid = meta["id"]
    folder = REPORTS_DIR / rid

    def log(message: str) -> None:
        with _lock:
            meta["progress"].append(message)

    def work() -> None:
        started = time.monotonic()
        try:
            if not _slot.acquire(blocking=False):
                log("Waiting for another report to finish …")
                _slot.acquire()
            try:
                with _lock:
                    meta["status"] = "running"
                    _write_json(folder / "meta.json", meta)
                log("Reading the samples …")
                result = build_aggregate(upload, options, log=log)
            finally:
                _slot.release()
            _write_json(folder / "tables.json", {k: result[k] for k in TABLES})
            with _lock:
                meta["summary"] = result["summary"]
                meta["notes"] = result["notes"]
                meta["counts"] = {k: len(result[k]["rows"]) for k in TABLES}
                meta["seconds"] = round(time.monotonic() - started, 1)
                meta["status"] = "done"
            log(f"Done: {meta['counts']['apis']} APIs and {meta['counts']['transactions']} transactions "
                f"from {result['summary']['samples_read']:,} samples")
        except AggregateError as exc:
            with _lock:
                meta["status"], meta["error"] = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 — shown to the user; the job must not die silently
            with _lock:
                meta["status"], meta["error"] = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            shutil.rmtree(upload.parent, ignore_errors=True)
            with _lock:
                _write_json(folder / "meta.json", meta)
                _active.pop(rid, None)

    threading.Thread(target=work, name=f"aggregate-report-{rid}", daemon=True).start()


def _flag(raw: str) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("")
async def create_report(
    file: UploadFile = File(...),
    title: str = Form(""),
    include_subresults: str = Form(""),
    include_empty_transactions: str = Form(""),
) -> dict[str, Any]:
    """Upload a JTL (CSV or XML, optionally .gz) and start building its Aggregate Report."""
    options = AggregateOptions(include_subresults=_flag(include_subresults),
                               include_empty_transactions=_flag(include_empty_transactions))
    name = Path(file.filename or "results.jtl").name or "results.jtl"
    rid = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    upload = REPORTS_DIR / rid / "upload" / name
    upload.parent.mkdir(parents=True, exist_ok=True)

    def save() -> int:
        with upload.open("wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        return upload.stat().st_size

    size = await asyncio.to_thread(save)
    problem = None                                     # reject what is not a result file now, not after a wait
    if size == 0:
        problem = f"{name} is empty"
    else:
        try:
            await asyncio.to_thread(sniff, upload)
        except IngestError as exc:
            problem = str(exc)
    if problem:
        shutil.rmtree(REPORTS_DIR / rid, ignore_errors=True)
        raise HTTPException(status_code=400, detail=problem)

    meta: dict[str, Any] = {
        "id": rid,
        "title": title.strip()[:200] or name.split(".")[0] or "Aggregate report",
        "source_file": name,
        "source_size": size,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "queued",
        "progress": [f"Received {name} ({size / 1_048_576:.1f} MB)"],
        "error": None,
        "options": {"include_subresults": options.include_subresults,
                    "include_empty_transactions": options.include_empty_transactions},
        "summary": None,
        "notes": None,
        "counts": None,
        "seconds": None,
    }
    with _lock:
        _active[rid] = meta
        _write_json(REPORTS_DIR / rid / "meta.json", meta)
    _start(meta, upload, options)
    return _public(_read_meta(rid) or meta)


@router.get("")
def list_reports() -> dict[str, Any]:
    """Aggregate reports, newest first (including ones still being built or that failed), without their tables."""
    reports = []
    if REPORTS_DIR.is_dir():
        for folder in REPORTS_DIR.iterdir():
            if folder.is_dir() and _ID_RE.match(folder.name):
                meta = _read_meta(folder.name)
                if meta:
                    reports.append(_public(meta))
    reports.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return {"reports": reports}


@router.get("/{report_id}")
def get_report(report_id: str, tables: bool = False) -> dict[str, Any]:
    """A report; with `tables`, its two tables too, each row with the values as JMeter's table shows them."""
    _folder(report_id)
    meta = _read_meta(report_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Unknown report")
    out = _public(meta)
    if tables and meta.get("status") == "done":
        data = _tables(report_id)
        for t in data.values():
            for row in [*t["rows"], *([t["total"]] if t.get("total") else [])]:
                row["shown"] = shown_values(row)
        out["tables"] = data
        out["columns"] = COLUMNS
    return out


@router.get("/{report_id}/{table}.csv")
def download_csv(report_id: str, table: str) -> Response:
    """One table as JMeter's Aggregate Report saves it (Save Table Data, with the header)."""
    _folder(report_id)
    if table not in TABLES:
        raise HTTPException(status_code=404, detail="Use apis.csv or transactions.csv")
    data = _tables(report_id)[table]
    if not data["rows"]:
        raise HTTPException(status_code=404, detail=f"This file has no {TABLES[table].lower()}")
    meta = _read_meta(report_id) or {}
    name = f"{_slug(meta.get('title') or '')}_aggregate_{table}.csv"
    return Response(table_csv(data).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{report_id}/aggregate.xlsx")
def download_xlsx(report_id: str) -> Response:
    """Both tables in one Excel workbook: an APIs sheet and a Transactions sheet."""
    _folder(report_id)
    data = _tables(report_id)
    meta = _read_meta(report_id) or {}
    sheets = []
    for key, title in TABLES.items():
        cells = table_cells(data[key])
        widths = [min(80, max([len(COLUMNS[0])] + [len(str(r[0])) for r in cells]) + 2)] + [12.0] * (len(COLUMNS) - 1)
        sheets.append(Sheet(title, COLUMNS, cells, XLSX_FORMATS, widths,
                            bold_rows={len(cells) - 1} if data[key].get("total") else set()))
    name = f"{_slug(meta.get('title') or '')}_aggregate_report.xlsx"
    return Response(workbook(sheets),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/{report_id}")
def delete_report(report_id: str) -> dict[str, Any]:
    folder = _folder(report_id)
    with _lock:
        if report_id in _active:
            raise HTTPException(status_code=409, detail="This report is still being built")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Unknown report")
    shutil.rmtree(folder)
    return {"deleted": report_id}
