"""
JMeter HTML reports in the web app: upload a result file (JTL), get JMeter's own
HTML dashboard for it. A standalone feature next to HAR → JMX and results analysis;
it needs JMeter but not ClickHouse.

Each report is a folder under REPORTS_DIR:
    <id>/meta.json      title, source file, options, status, progress, summary
    <id>/report/        JMeter's dashboard (index.html, content/, sbadmin2-…/, statistics.json)
    <id>/jmeter.log     JMeter's log of the generation, kept for troubleshooting
    <id>/<id>.zip       the dashboard as a ZIP, built on the first download
    <id>/graphs/        the five report graphs as JPEGs, captured from the dashboard (dashboard_graphs.py)
The uploaded file and any rewritten CSV are deleted once the report is generated.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from projectstack.runner import dashboard_graphs as dg

from projectstack.results.ingest import IngestError, sniff
from projectstack.runner.html_report import (
    JMETER_DEFAULT_GRANULARITY_MS, ReportError, ReportOptions, fmt_granularity, generate_html_report,
)
from projectstack.runner.jmeter_runner import find_jmeter

router = APIRouter(prefix="/api/html-reports", tags=["html-reports"])

REPORTS_DIR = Path.home() / ".projectstack" / "html-reports"

_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_active: dict[str, dict[str, Any]] = {}       # id -> meta of reports queued or being generated
_lock = threading.Lock()
_slot = threading.Semaphore(1)                # one JMeter report generation at a time
_capture_locks: dict[str, threading.Lock] = {}  # report id -> lock: one graph capture per report at a time


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _folder(report_id: str) -> Path:
    if not _ID_RE.match(report_id):
        raise HTTPException(status_code=404, detail="Unknown report")
    return REPORTS_DIR / report_id


def _write_meta(meta: dict[str, Any]) -> None:
    folder = REPORTS_DIR / meta["id"]
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(folder / "meta.json")


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
        meta["error"] = "Interrupted: ProjectStack stopped while this report was being generated. Upload the file again."
    return meta


def _public(meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta)
    done = meta.get("status") == "done"
    base = f"/api/html-reports/{meta['id']}"
    out["view_url"] = f"{base}/view/index.html" if done else None
    out["download_url"] = f"{base}/download" if done else None
    out["graphs_url"] = f"{base}/graphs" if done else None          # <graphs_url>/<slug>.jpg
    out["graphs_zip_url"] = f"{base}/graphs.zip" if done else None
    out.setdefault("graphs_error", None)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")[:80] or "jmeter-report"


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------

def _start(meta: dict[str, Any], upload: Path, options: ReportOptions) -> None:
    rid = meta["id"]
    folder = REPORTS_DIR / rid
    work = folder / "work"

    def log(message: str) -> None:
        with _lock:
            meta["progress"].append(message)

    def work_fn() -> None:
        started = time.monotonic()
        try:
            if not _slot.acquire(blocking=False):
                log("Waiting for another report to finish …")
                _slot.acquire()
            try:
                with _lock:
                    meta["status"] = "running"
                _write_meta(meta)
                result = generate_html_report(upload, folder / "report", work, options, log=log)
            finally:
                _slot.release()
            summary = result["summary"]
            used: ReportOptions = result["options"]
            log("Capturing the five graphs from the dashboard …")
            try:
                dg.capture_graphs(folder / "report", folder / "graphs")
            except Exception as exc:  # noqa: BLE001 — the report is fine without them; say why they are missing
                with _lock:
                    meta["graphs_error"] = str(exc)
                log(f"Graphs not captured: {exc}")
            with _lock:
                meta["summary"] = summary
                meta["options"]["granularity_ms"] = used.granularity_ms or JMETER_DEFAULT_GRANULARITY_MS
                meta["seconds"] = round(time.monotonic() - started, 1)
                meta["status"] = "done"
            log(f"Done: {summary['samples']:,} samples across {summary['labels']} labels "
                f"in {meta['seconds']:.0f} s")
        except ReportError as exc:
            with _lock:
                meta["status"], meta["error"] = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 — shown to the user; the job must not die silently
            with _lock:
                meta["status"], meta["error"] = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            log_file = work / "jmeter.log"
            if log_file.exists():
                shutil.move(str(log_file), str(folder / "jmeter.log"))
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(upload.parent, ignore_errors=True)
            if meta["status"] == "failed":
                shutil.rmtree(folder / "report", ignore_errors=True)
            with _lock:
                _write_meta(meta)
                _active.pop(rid, None)

    threading.Thread(target=work_fn, name=f"html-report-{rid}", daemon=True).start()


def _int_field(raw: str, name: str, low: int, high: int) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise HTTPException(status_code=400, detail=f"{name} must be between {low:,} and {high:,}")
    return value


def _options(title: str, apdex_satisfied: str, apdex_tolerated: str, granularity: str,
             label_filter: str) -> ReportOptions:
    satisfied = _int_field(apdex_satisfied, "Apdex satisfied threshold", 1, 3_600_000)
    tolerated = _int_field(apdex_tolerated, "Apdex tolerated threshold", 1, 3_600_000)
    if satisfied is not None and tolerated is None:
        tolerated = 4 * satisfied                        # Apdex defines F = 4T
    if tolerated is not None and tolerated <= (satisfied if satisfied is not None else 500):
        raise HTTPException(status_code=400, detail="The Apdex tolerated threshold must be greater than the satisfied threshold")
    choice = (granularity or "").strip().lower()
    auto = choice == "auto"
    gran = None if choice in ("", "auto") else _int_field(granularity, "Graph granularity", 1000, 86_400_000)
    return ReportOptions(title=title.strip()[:200], apdex_satisfied_ms=satisfied, apdex_tolerated_ms=tolerated,
                         granularity_ms=gran, auto_granularity=auto, label_filter=label_filter.strip())


def _check(path: Path) -> None:
    """Format check only (the first MB): the same one the generation starts with."""
    try:
        sniff(path)
    except IngestError as exc:
        raise ReportError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("")
async def create_report(
    file: UploadFile = File(...),
    title: str = Form(""),
    apdex_satisfied: str = Form(""),
    apdex_tolerated: str = Form(""),
    granularity: str = Form(""),
    label_filter: str = Form(""),
) -> dict[str, Any]:
    """Upload a JTL (CSV or XML, optionally .gz) and start generating its JMeter HTML dashboard."""
    if not find_jmeter():
        raise HTTPException(status_code=400, detail="JMeter was not found. Set its location in Settings → JMeter path.")
    options = _options(title, apdex_satisfied, apdex_tolerated, granularity, label_filter)

    name = Path(file.filename or "results.jtl").name or "results.jtl"
    rid = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    upload = REPORTS_DIR / rid / "upload" / name      # its own folder: a rewritten CSV goes to work/, never over it
    upload.parent.mkdir(parents=True, exist_ok=True)

    def save() -> int:
        with upload.open("wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        return upload.stat().st_size

    size = await asyncio.to_thread(save)
    # Reject what is not a result file now, rather than after a wait
    problem = None
    if size == 0:
        problem = f"{name} is empty"
    else:
        try:
            await asyncio.to_thread(_check, upload)
        except ReportError as exc:
            problem = str(exc)
    if problem:
        shutil.rmtree(REPORTS_DIR / rid, ignore_errors=True)
        raise HTTPException(status_code=400, detail=problem)

    options.title = options.title or Path(name).name.split(".")[0]
    meta: dict[str, Any] = {
        "id": rid,
        "title": options.title,
        "source_file": name,
        "source_size": size,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "queued",
        "progress": [f"Received {name} ({size / 1_048_576:.1f} MB)"],
        "error": None,
        "options": {
            "apdex_satisfied_ms": options.apdex_satisfied_ms,
            "apdex_tolerated_ms": options.apdex_tolerated_ms,
            "granularity_ms": options.granularity_ms,
            "label_filter": options.label_filter,
        },
        "summary": None,
        "seconds": None,
    }
    if options.granularity_ms:
        meta["progress"].append(f"Graph granularity: {fmt_granularity(options.granularity_ms)}")
    with _lock:
        _active[rid] = meta
        _write_meta(meta)
    _start(meta, upload, options)
    return _public(_read_meta(rid) or meta)


@router.get("")
def list_reports() -> dict[str, Any]:
    """Generated reports, newest first (including ones still being generated or that failed)."""
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
def get_report(report_id: str) -> dict[str, Any]:
    _folder(report_id)
    meta = _read_meta(report_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Unknown report")
    return _public(meta)


@router.get("/{report_id}/view/{path:path}")
def view_report(report_id: str, path: str):
    """The dashboard's files. Its links are relative, so it works under this URL as it is."""
    root = (_folder(report_id) / "report").resolve()
    target = (root / (path or "index.html")).resolve()
    if target.is_dir():
        target = target / "index.html"
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


def _report_graphs(report_id: str) -> tuple[Path, dict[str, Any]]:
    """The report's graphs folder, capturing the graphs first for reports made before they were captured."""
    folder = _folder(report_id)
    meta = _read_meta(report_id)
    if not meta or meta.get("status") != "done" or not (folder / "report" / "index.html").is_file():
        raise HTTPException(status_code=404, detail="This report is not available")
    out = folder / "graphs"
    if not dg.graph_files(out):
        with _lock:
            lock = _capture_locks.setdefault(report_id, threading.Lock())
        with lock:                                            # the five previews ask at once: capture once
            if not dg.graph_files(out):
                try:
                    dg.capture_graphs(folder / "report", out)
                except dg.GraphCaptureError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
    return out, meta


@router.get("/{report_id}/graphs/{name}")
def report_graph(report_id: str, name: str, download: bool = False) -> FileResponse:
    """One graph as a JPEG, exactly as the dashboard shows it: running-vusers, hits-per-second,
    throughput, response-time or errors, with .jpg (`download` makes the browser save it)."""
    _folder(report_id)
    slug = dg.slug_from_name(name)
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown graph {name!r}: use one of {', '.join(dg.SLUGS)} with .jpg")
    out, meta = _report_graphs(report_id)
    filename = f"{_slug(meta.get('title') or 'jmeter-report')}_{slug}.jpg"
    return FileResponse(out / f"{slug}.jpg", media_type="image/jpeg", headers={
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{filename}"',
        "Cache-Control": "no-cache"})       # checked each time: a graph is captured again when its panel changes


@router.get("/{report_id}/graphs.zip")
def report_graphs_zip(report_id: str) -> Response:
    """The five graphs as JPEGs in one ZIP."""
    out, meta = _report_graphs(report_id)
    prefix = _slug(meta.get("title") or "jmeter-report")
    return Response(dg.zip_graphs(out, prefix), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{prefix}_graphs.zip"', "Cache-Control": "no-cache"})


@router.get("/{report_id}/download")
def download_report(report_id: str):
    """The dashboard as a ZIP with one top-level folder, built once and kept."""
    folder = _folder(report_id)
    meta = _read_meta(report_id)
    report = folder / "report"
    if not meta or meta.get("status") != "done" or not (report / "index.html").is_file():
        raise HTTPException(status_code=404, detail="This report is not available")
    name = _slug(meta.get("title") or meta.get("source_file") or "jmeter-report")
    zip_path = folder / f"{report_id}.zip"
    if not zip_path.exists():
        tmp = folder / f"{report_id}.zip.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(report.rglob("*")):
                if f.is_file():
                    zf.write(f, f"{name}/{f.relative_to(report)}")
        tmp.replace(zip_path)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{name}-jmeter-report.zip")


@router.delete("/{report_id}")
def delete_report(report_id: str) -> dict[str, Any]:
    folder = _folder(report_id)
    with _lock:
        if report_id in _active:
            raise HTTPException(status_code=409, detail="This report is still being generated")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Unknown report")
    shutil.rmtree(folder)
    return {"deleted": report_id}
