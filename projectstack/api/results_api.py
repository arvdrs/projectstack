"""
Results analysis for the web app: the same engine as `pstack analyze / report /
compare / ask`, behind HTTP.

Analyses run as background jobs (a large JTL takes a while to load), so the
browser uploads the file, gets a job id and polls it. Everything else reads the
run's artifacts (findings.json, metrics.json) or queries ClickHouse.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from projectstack.config import IN_DOCKER, load_settings

router = APIRouter(prefix="/api/results", tags=["results"])

UPLOAD_DIR = Path.home() / ".projectstack" / "uploads"
CLICKHOUSE_HINT = ("docker compose up -d clickhouse" if IN_DOCKER
                   else "docker compose -f deploy/clickhouse/docker-compose.yml up -d")


# ---------------------------------------------------------------------------
# Background analysis jobs
# ---------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    source: str
    status: str = "queued"                 # queued | running | done | failed
    progress: list[str] = field(default_factory=list)
    run_id: str | None = None
    error: str | None = None
    session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def log(self, message: str) -> None:
        self.progress.append(message)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _settings() -> dict[str, str]:
    return load_settings()


def _db():
    from projectstack.results.db import ResultsDBError, ResultsDBUnavailable, connect
    try:
        return connect(_settings())
    except ResultsDBUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"{exc} — start it with: {CLICKHOUSE_HINT}") from exc
    except ResultsDBError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _parse_sla(raw: str | dict | None) -> str | dict | None:
    """The web app sends an SLA config as JSON; a `p95=3000,err=1` spec works too."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"SLA is not valid JSON: {exc}") from exc
    return text


def upload_path(name: str) -> Path:
    """A fresh folder per upload, so the file keeps its own name (shown in progress and in the run's details)."""
    folder = UPLOAD_DIR / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def start_job(path: Path, *, source: str, scenario: str | None = None, tag: str = "", sla: str | dict | None = None,
              steady: str | None = None, baseline: str | None = None, jmx: str | None = None,
              cleanup: bool = True, session_id: str | None = None) -> Job:
    """Analyse `path` in a background thread; returns the job to poll."""
    job = Job(id=secrets.token_hex(6), source=source, session_id=session_id)
    with _jobs_lock:
        _jobs[job.id] = job
        for old in sorted(_jobs.values(), key=lambda j: j.created_at)[:-100]:   # keep the last 100
            _jobs.pop(old.id, None)

    def work() -> None:
        from projectstack.results.analyze import analyze_file
        from projectstack.results.db import ResultsDBError, ResultsDBUnavailable
        from projectstack.results.ingest import IngestError
        job.status = "running"
        try:
            result = analyze_file(path, steady=steady or None, sla=sla, tag=tag, scenario=scenario or None,
                                  baseline=baseline or None, jmx=jmx, settings=_settings(), progress=job.log)
            job.run_id = result.run_id
            job.log(f"Done: {result.meta['run']['samples']:,} samples analysed")
            _auto_fetch_server_metrics(result, job)
            job.status = "done"
        except ResultsDBUnavailable as exc:
            job.error, job.status = f"{exc} — start it with: {CLICKHOUSE_HINT}", "failed"
        except ResultsDBError as exc:
            job.error, job.status = f"The results database could not process this file: {exc}", "failed"
        except (IngestError, ValueError) as exc:
            job.error, job.status = str(exc), "failed"
        except Exception as exc:  # noqa: BLE001 — shown to the user, the job must not die silently
            job.error, job.status = f"{type(exc).__name__}: {exc}", "failed"
        finally:
            if cleanup:
                path.unlink(missing_ok=True)
                if path.parent != UPLOAD_DIR and path.parent.parent == UPLOAD_DIR:
                    shutil.rmtree(path.parent, ignore_errors=True)

    threading.Thread(target=work, name=f"analyze-{job.id}", daemon=True).start()
    return job


def _auto_fetch_server_metrics(result, job: "Job") -> None:
    """When the scenario has cloud resources saved, fetch their metrics for the new run (never fails the job)."""
    settings = _settings()
    if str(settings.get("cloud_auto_fetch", "true")).lower() not in ("1", "true", "yes"):
        return
    from projectstack.results.cloud import CloudError, load_targets
    from projectstack.results.cloud.analysis import fetch_and_attach
    resources = load_targets(settings, result.meta.get("scenario", ""))
    if not resources:
        return
    job.log(f"Fetching server metrics for {len(resources)} cloud resource(s) …")
    try:
        from projectstack.results.db import connect
        info = fetch_and_attach(connect(settings), result.folder, settings, resources)
        job.log(f"Server metrics: {info['series']} series, {info['findings']} finding(s)")
    except CloudError as exc:
        job.log(f"Server metrics not fetched: {exc}")
    except Exception as exc:  # noqa: BLE001 — the analysis itself succeeded
        job.log(f"Server metrics not fetched: {type(exc).__name__}: {exc}")


@router.get("/status")
def results_status() -> dict[str, Any]:
    """Whether the results database is reachable (the page shows how to start it when not)."""
    from projectstack.results.db import ResultsDBError, connect
    try:
        db = connect(_settings())
        version = db.rows("SELECT version()")[0][0]
        runs = db.rows(f"SELECT uniqExact(run_id) FROM {db.db}.runs FINAL WHERE status = 'ready'")[0][0]
        return {"available": True, "version": version, "server": db.cfg.display, "runs": int(runs)}
    except ResultsDBError as exc:
        return {"available": False, "message": str(exc), "hint": CLICKHOUSE_HINT}


@router.post("/analyze")
async def analyze_upload(
    file: UploadFile = File(...),
    scenario: str = Form(""),
    tag: str = Form(""),
    sla: str = Form(""),
    steady: str = Form(""),
    baseline: str = Form(""),
) -> dict[str, Any]:
    """Upload a JTL (CSV or XML, optionally .gz) and start analysing it."""
    name = Path(file.filename or "results.jtl").name or "results.jtl"
    path = upload_path(name)

    def save() -> int:
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        return path.stat().st_size

    size = await asyncio.to_thread(save)
    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{name} is empty")
    sla_value = _parse_sla(sla)
    job = start_job(path, source=name, scenario=scenario.strip() or Path(name).name.split(".")[0], tag=tag.strip(),
                    sla=sla_value, steady=steady.strip(), baseline=baseline.strip())
    job.log(f"Received {name} ({size / 1_048_576:.1f} MB)")
    return asdict(job)


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown analysis job (the server may have restarted)")
    return asdict(job)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def _iso(v: Any) -> Any:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return v


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=500), scenario: str | None = None) -> dict[str, Any]:
    from projectstack.results import store
    rows = store.list_runs(_db(), limit=limit, scenario=scenario or None)
    return {"runs": [{k: _iso(v) for k, v in r.items()} for r in rows]}


def _resolve(run_ref: str) -> tuple[str, Path]:
    from projectstack.results import store
    try:
        run_id = store.resolve_run(_db(), run_ref)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run_id, Path(_settings()["runs_dir"]) / run_id


def _findings(folder: Path) -> dict[str, Any]:
    path = folder / "findings.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"findings.json for this run is missing from {folder}")
    from projectstack.results import jmeter_stats
    return jmeter_stats.ensure_total(json.loads(path.read_text()), folder, _db)


def _cached_ai(folder: Path, audience: str, doc: dict) -> dict[str, Any] | None:
    """A saved AI narrative, if it still matches the run's findings."""
    from projectstack.results.report import _sha
    path = folder / f"narrative_{audience}.json"
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if saved.get("source") != "llm" or saved.get("findings_sha256") != _sha(doc):
        return None
    return {"content": saved["content"], "provider": saved.get("provider"), "prompt": saved.get("prompt"),
            "numbers": saved.get("numbers"), "generated_at": saved.get("generated_at")}


def _download_name(folder: Path, run_id: str, kind: str, ext: str) -> str:
    """"Checkout-load-test_20261117-0441_technical-report.html": the scenario first, so saved files sort by test."""
    try:
        scenario = json.loads((folder / "findings.json").read_text())["run"].get("scenario", "")
    except (OSError, ValueError, KeyError):
        scenario = ""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", scenario).strip("-._")[:60]
    return f"{slug + '_' if slug else ''}{run_id}_{kind}.{ext}"


def _themed(html: str, theme: str | None) -> HTMLResponse:
    """A report page in the web app's light or dark mode (the saved file keeps following the system)."""
    if theme in ("light", "dark"):
        html = html.replace('<html lang="en">', f'<html lang="en" data-theme="{theme}">', 1)
    return HTMLResponse(html)


def observations(doc: dict[str, Any]) -> dict[str, Any]:
    """High-level observations in plain language (rule-based, so always available and free)."""
    from projectstack.results.narrative import from_rules
    return from_rules(doc, "exec").content


def _test_context(folder: Path, doc: dict[str, Any]) -> dict[str, Any]:
    from projectstack.results import highlights
    return highlights.resolve_context(folder, doc["run"].get("scenario", ""), Path(_settings()["runs_dir"]))


def _highlights(folder: Path, doc: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """The Performance Highlights & Observations section: rule-based, and the saved AI version if still current."""
    from projectstack.results import highlights
    return {"highlights": highlights.rules(doc, ctx), "highlights_ai": highlights.cached(folder, doc, ctx),
            "context": ctx}


@router.get("/runs/{run_ref}")
def run_detail(run_ref: str) -> dict[str, Any]:
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    meta = json.loads((folder / "meta.json").read_text()) if (folder / "meta.json").exists() else {}
    return {
        **doc,
        "findings": [{**f, "n": i} for i, f in enumerate(doc.get("findings", []), 1)],
        "created_at": meta.get("created_at"),
        "source_file": Path((meta.get("source") or {}).get("file", "")).name,
        "has_plan": (folder / "plan.jmx").exists(),
        "observations": observations(doc),
        "observations_ai": _cached_ai(folder, "exec", doc),
        **_highlights(folder, doc, _test_context(folder, doc)),
        "folder": str(folder),
    }


class AiRequest(BaseModel):
    provider: str | None = None
    fresh: bool = False


class ContextRequest(BaseModel):
    region: str = ""
    test_type: str = ""
    notes: str = ""


@router.put("/runs/{run_ref}/context")
def save_test_context(run_ref: str, body: ContextRequest) -> dict[str, Any]:
    """Save the run's test context (region, test type, notes); returns the section rewritten with it."""
    from projectstack.results import highlights
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    highlights.save_context(folder, body.model_dump())
    return _highlights(folder, doc, _test_context(folder, doc))


@router.post("/runs/{run_ref}/highlights")
async def ai_highlights(run_ref: str, body: AiRequest) -> dict[str, Any]:
    """Ask the AI provider to write the section (findings.json, the computed facts and the notes are sent)."""
    from projectstack.results import highlights
    from projectstack.results.report import ReportError
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    ctx = _test_context(folder, doc)
    try:
        section, reused, warnings = await asyncio.to_thread(
            highlights.get, doc, folder, ctx, use_llm=True, provider_spec=body.provider, strict=False,
            fresh=body.fresh, settings=_settings())
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"content": section.content, "source": section.source, "provider": section.provider,
            "prompt": section.prompt, "numbers": section.numbers.as_dict() if section.numbers else None,
            "note": section.note, "reused": reused, "warnings": warnings}


@router.post("/runs/{run_ref}/observations")
async def ai_observations(run_ref: str, body: AiRequest) -> dict[str, Any]:
    """Ask the AI provider for the executive narrative (only findings.json is sent)."""
    from projectstack.results.report import ReportError, get_narrative
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    try:
        narrative, reused, warnings = await asyncio.to_thread(
            get_narrative, doc, folder, "exec", use_llm=True, provider_spec=body.provider, strict=False,
            fresh=body.fresh, settings=_settings(), progress=lambda _: None)
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"content": narrative.content, "source": narrative.source, "provider": narrative.provider,
            "prompt": narrative.prompt, "numbers": narrative.numbers.as_dict() if narrative.numbers else None,
            "note": narrative.note, "reused": reused, "warnings": warnings}


@router.get("/runs/{run_ref}/report")
async def run_report(run_ref: str, audience: str = Query("tech", pattern="^(tech|exec)$"),
                     format: str = Query("html", pattern="^(html|md|docx|doc)$"), ai: bool = False,
                     download: bool = False, theme: str | None = Query(None, pattern="^(light|dark)$")):
    """The technical or executive report. Without ai, the rule-based narrative (nothing leaves the machine)."""
    from projectstack.results.report import ReportError, build_report
    run_id, folder = _resolve(run_ref)
    if format in ("doc", "docx"):                # the performance report in Word, in the company template
        return await _word_report(run_id, folder, ai, format)
    # Kept apart from the CLI's report_<audience>.<format> so the web app never overwrites those
    out = folder / "web" / f"report_{audience}{'_ai' if ai else ''}.{format}"
    try:
        await asyncio.to_thread(build_report, str(folder), audience=audience, formats=(format,), use_llm=ai,
                                out=str(out), settings=_settings())
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if format == "html" and not download:
        return _themed(out.read_text(encoding="utf-8"), theme)
    media = "text/html" if format == "html" else "text/markdown"
    kind = "technical-report" if audience == "tech" else "executive-summary"
    return FileResponse(out, media_type=media, filename=_download_name(folder, run_id, kind, format))


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WORD_TYPES = {"docx": DOCX_TYPE, "doc": "application/msword"}


async def _word_report(run_id: str, folder: Path, ai: bool, ext: str = "docx"):
    from projectstack.results import office, word
    from projectstack.results.report import ReportError
    doc = _findings(folder)
    settings = _settings()
    if ext == "doc" and not office.find_soffice(settings):
        raise HTTPException(status_code=503, detail="Word 97–2003 (.doc) reports need LibreOffice, which is not "
                            f"installed on this machine — run: {office.INSTALL_HINT}. The .docx download works without it.")
    build = word.build_doc if ext == "doc" else word.build
    try:
        data, warnings = await asyncio.to_thread(build, folder, doc, settings=settings, use_llm=ai)
    except (ReportError, word.WordError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    out = folder / "web" / f"report_performance{'_ai' if ai else ''}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    headers = {"X-Report-Warnings": "; ".join(warnings)[:900].encode("ascii", "replace").decode()} if warnings else None
    return FileResponse(out, media_type=WORD_TYPES[ext], filename=_download_name(folder, run_id, "performance-report", ext),
                        headers=headers)


@router.get("/report-template")
def report_template() -> dict[str, Any]:
    """The company Word template reports are built in (None: the built-in layout), and the placeholders it can use."""
    from projectstack.results import office, word
    return {"template": word.template_info(_settings()), "placeholders": word.PLACEHOLDERS, "marker": word.MARKER,
            "doc": office.status(_settings())}


@router.post("/report-template")
async def upload_report_template(file: UploadFile = File(...)) -> dict[str, Any]:
    from projectstack.results import word
    data = await file.read(word.MAX_TEMPLATE_BYTES + 1)
    try:
        info = await asyncio.to_thread(word.save_template, _settings(), file.filename or "template.docx", data)
    except word.WordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**report_template(), "template": info}


@router.delete("/report-template")
def delete_report_template() -> dict[str, Any]:
    from projectstack.results import word
    word.remove_template(_settings())
    return report_template()


@router.get("/report-template/starter")
def starter_report_template():
    """A plain template to brand: cover page with placeholders, header, footer and the [[REPORT]] marker."""
    from fastapi.responses import Response
    from projectstack.results import word
    return Response(word.starter_template(), media_type=DOCX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="projectstack-report-template.docx"'})


@router.get("/runs/{run_ref}/label")
def label_detail(run_ref: str, name: str) -> dict[str, Any]:
    """Everything about one request or transaction, with a plain-language synthesis."""
    from projectstack.results import labels
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    db = _db()
    base_metrics, base_info = labels.load_baseline(doc, folder.parent, db)
    try:
        return labels.detail(db, run_id, name, doc, base_metrics, base_info)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_ref}/labels")
def label_list(run_ref: str) -> dict[str, Any]:
    """Every label of the run with its kind and steady-state statistics (metrics.json covers all of them)."""
    run_id, folder = _resolve(run_ref)
    metrics_path = folder / "metrics.json"
    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail="metrics.json for this run is missing")
    metrics = json.loads(metrics_path.read_text())
    rows = [{"kind": k, **r} for k in ("transaction", "request") for r in (metrics.get("labels") or {}).get(k, [])]
    return {"run_id": run_id, "labels": rows}


@router.get("/runs/{run_ref}/jmeter-statistics")
def jmeter_statistics(run_ref: str) -> dict[str, Any]:
    """The Statistics table of JMeter's HTML report, from the run's samples (whole run, JMeter's rules)."""
    from projectstack.results import jmeter_stats
    run_id, folder = _resolve(run_ref)
    try:
        return {"run_id": run_id, **jmeter_stats.load(_db(), run_id, folder)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_ref}/labels-report")
async def labels_report(run_ref: str, format: str = Query("html", pattern="^(html|md)$"),
                        limit: int = Query(40, ge=1, le=500), download: bool = False,
                        theme: str | None = Query(None, pattern="^(light|dark)$")):
    """Synthesis report for the run's transactions and main requests (rule-based; no AI)."""
    from projectstack.results import labels
    run_id, folder = _resolve(run_ref)
    _findings(folder)
    db = _db()
    text, _ = await asyncio.to_thread(labels.build_report, db, folder, None, limit, format)
    out = folder / "web" / f"report_labels.{format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if format == "html" and not download:
        return _themed(text, theme)
    return FileResponse(out, media_type="text/html" if format == "html" else "text/markdown",
                        filename=_download_name(folder, run_id, "requests-and-transactions", format))


# ---------------------------------------------------------------------------
# Server metrics from AWS CloudWatch / Azure Monitor
# ---------------------------------------------------------------------------

def _cloud_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/cloud/catalog")
def cloud_catalog() -> dict[str, Any]:
    from projectstack.results.cloud.presets import CATEGORIES, catalog
    return {"types": catalog(), "categories": CATEGORIES}


@router.get("/cloud/status")
def cloud_status() -> dict[str, Any]:
    """Which providers are usable and how they would sign in (no network calls)."""
    from projectstack.results.cloud import configured
    return configured(_settings())


class CloudCheck(BaseModel):
    provider: str


@router.post("/cloud/check")
async def cloud_check(body: CloudCheck) -> dict[str, Any]:
    """Sign in and say as whom (AWS: the account and identity; Azure: the subscription)."""
    from projectstack.results.cloud import CloudError, get_provider
    try:
        return await asyncio.to_thread(lambda: get_provider(body.provider, _settings()).check())
    except CloudError as exc:
        raise _cloud_error(exc) from exc


@router.get("/cloud/discover")
async def cloud_discover(provider: str, types: str = "", region: str = "") -> dict[str, Any]:
    from projectstack.results.cloud import CloudError, get_provider
    wanted = [t.strip() for t in types.split(",") if t.strip()] or None
    try:
        found, warnings = await asyncio.to_thread(lambda: get_provider(provider, _settings()).discover(wanted, region))
    except CloudError as exc:
        raise _cloud_error(exc) from exc
    return {"resources": [r.__dict__ for r in found], "warnings": warnings}


@router.get("/cloud/targets")
def cloud_targets(scenario: str) -> dict[str, Any]:
    from projectstack.results.cloud import load_targets
    return {"scenario": scenario, "resources": [r.__dict__ for r in load_targets(_settings(), scenario)]}


class TargetsUpdate(BaseModel):
    scenario: str
    resources: list[dict[str, Any]]


@router.put("/cloud/targets")
def save_cloud_targets(body: TargetsUpdate) -> dict[str, Any]:
    from projectstack.results.cloud import CloudError, Resource, save_targets
    try:
        resources = [Resource.from_dict(r) for r in body.resources]
    except (CloudError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_targets(_settings(), body.scenario, resources)
    return {"scenario": body.scenario, "resources": [r.__dict__ for r in resources]}


class ServerFetch(BaseModel):
    resources: list[dict[str, Any]] | None = None
    save: bool = True            # remember these resources for the scenario


@router.post("/runs/{run_ref}/server-metrics")
async def fetch_server_metrics(run_ref: str, body: ServerFetch) -> dict[str, Any]:
    """Read the resources' metrics for this run's window from AWS / Azure and add them to the analysis."""
    from projectstack.results.cloud import CloudError, Resource, save_targets
    from projectstack.results.cloud.analysis import fetch_and_attach
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    settings = _settings()
    try:
        resources = [Resource.from_dict(r) for r in body.resources] if body.resources is not None else None
        if resources and body.save:
            save_targets(settings, doc["run"].get("scenario", ""), resources)
        return await asyncio.to_thread(fetch_and_attach, _db(), folder, settings, resources)
    except (CloudError, KeyError) as exc:
        raise _cloud_error(exc) from exc


@router.get("/runs/{run_ref}/server-metrics")
def get_server_metrics(run_ref: str) -> dict[str, Any]:
    from projectstack.results import server_load
    from projectstack.results.cloud.analysis import read
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    data = read(folder)
    return {"run_id": run_id, "scenario": doc["run"].get("scenario", ""), "available": data is not None,
            "summary": doc.get("server"), "series": (data or {}).get("series", []),
            "resources": (data or {}).get("resources", []), "missing": (data or {}).get("missing", []),
            "warnings": (data or {}).get("warnings", []), "fetched_at": (data or {}).get("fetched_at"),
            "steady": doc.get("steady"), "duration_s": doc["run"]["duration_s"],
            "relation": server_load.from_cloud(data, doc),
            "findings": [{**f, "n": i} for i, f in enumerate(doc.get("findings", []), 1) if f["type"] == "server"]}


# ---------------------------------------------------------------------------
# Server-metric screenshots and their AI reading
# ---------------------------------------------------------------------------

@dataclass
class ShotJob:
    """A screenshot reading in the background (several requests to the AI provider)."""
    id: str
    run_id: str
    status: str = "running"              # running | done | failed
    done: int = 0
    total: int = 0
    message: str = "Starting…"
    error: str | None = None
    result: dict[str, Any] | None = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "run_id": self.run_id, "status": self.status, "done": self.done, "total": self.total,
                "message": self.message, "error": self.error, "result": self.result}


_shot_jobs: dict[str, ShotJob] = {}
_shot_jobs_lock = threading.Lock()


def _running_shot_job(run_id: str) -> ShotJob | None:
    with _shot_jobs_lock:
        return next((j for j in _shot_jobs.values() if j.run_id == run_id and j.status == "running"), None)


def _shots_state(folder: Path, doc: dict[str, Any]) -> dict[str, Any]:
    from projectstack.llm.registry import vision_choices
    from projectstack.results import screenshots
    job = _running_shot_job(doc["run"]["run_id"])
    return {**screenshots.state(folder, doc, _test_context(folder, doc)), "providers": vision_choices(_settings()),
            "job": job.view() if job else None}


@router.get("/runs/{run_ref}/screenshots")
def list_screenshots(run_ref: str) -> dict[str, Any]:
    """The run's screenshots, the saved AI reading (marked stale when out of date) and the providers that can read them."""
    run_id, folder = _resolve(run_ref)
    return _shots_state(folder, _findings(folder))


@router.post("/runs/{run_ref}/screenshots")
async def add_screenshots(run_ref: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Attach screenshots (PNG, JPEG, WebP or GIF). Files that cannot be added are reported, the rest are kept."""
    from projectstack.results import screenshots
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    errors: list[str] = []
    room = screenshots.MAX_FILES - len(screenshots.list_items(folder))
    over = [f.filename or "screenshot" for f in files[max(room, 0):]]
    for f in files[:max(room, 0)]:
        data = await f.read(screenshots.MAX_BYTES + 1)
        try:
            await asyncio.to_thread(screenshots.add, folder, f.filename or "screenshot", data)
        except screenshots.ScreenshotError as exc:
            errors.append(str(exc))
    if over:                                 # one message for all the files that did not fit
        errors.append(f"{len(over)} file{'s' if len(over) > 1 else ''} not added, as a run takes up to "
                      f"{screenshots.MAX_FILES} screenshots ({', '.join(over[:5])}{', …' if len(over) > 5 else ''}): "
                      "remove some first")
    if len(errors) - (1 if over else 0) + len(over) >= len(files):      # nothing was added
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return {**_shots_state(folder, doc), "errors": errors}


@router.get("/runs/{run_ref}/screenshots/{shot_id}/image")
def screenshot_image(run_ref: str, shot_id: str):
    from fastapi.responses import Response
    from projectstack.results import screenshots
    run_id, folder = _resolve(run_ref)
    try:
        data, media_type = screenshots.image(folder, shot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(data, media_type=media_type,
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


class CaptionRequest(BaseModel):
    caption: str = ""


@router.patch("/runs/{run_ref}/screenshots/{shot_id}")
def caption_screenshot(run_ref: str, shot_id: str, body: CaptionRequest) -> dict[str, Any]:
    from projectstack.results import screenshots
    run_id, folder = _resolve(run_ref)
    try:
        screenshots.update(folder, shot_id, body.caption)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _shots_state(folder, _findings(folder))


@router.delete("/runs/{run_ref}/screenshots/{shot_id}")
def delete_screenshot(run_ref: str, shot_id: str) -> dict[str, Any]:
    from projectstack.results import screenshots
    run_id, folder = _resolve(run_ref)
    try:
        screenshots.remove(folder, shot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _shots_state(folder, _findings(folder))


class ScreenshotReadRequest(BaseModel):
    provider: str | None = None          # "gemini", "ollama/gemma4" …; default: the screenshots task's provider
    fresh: bool = False
    time_zone: str = ""                  # the browser's IANA zone, as monitoring tools often show local time


@router.post("/runs/{run_ref}/screenshots/analysis")
def read_screenshots(run_ref: str, body: ScreenshotReadRequest) -> dict[str, Any]:
    """Start reading the screenshots with a vision model in the background; poll the job for progress.

    The screenshots go to the chosen provider with the run's timeline and notes, a few per request.
    A reading already running for the run is returned instead of starting another.
    """
    from projectstack.llm.base import LLMConfigError, LLMError
    from projectstack.llm.registry import provider_for_task
    from projectstack.results import screenshots
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    if not screenshots.list_items(folder):
        raise HTTPException(status_code=400, detail="Attach at least one screenshot first")
    try:
        provider_for_task("screenshots", _settings(), body.provider)      # a provider that is not set up: say so now
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    running = _running_shot_job(run_id)
    if running:
        return running.view()
    job = ShotJob(id=secrets.token_hex(6), run_id=run_id)
    with _shot_jobs_lock:
        _shot_jobs[job.id] = job

    def progress(done: int, total: int, message: str) -> None:
        job.done, job.total, job.message = done, total, message

    def work() -> None:
        try:
            job.result = screenshots.analyse(doc, folder, _test_context(folder, doc), settings=_settings(),
                                             provider_spec=body.provider, fresh=body.fresh,
                                             tz_name=body.time_zone[:64], progress=progress)
            job.status = "done"
        except (screenshots.ScreenshotError, LLMConfigError) as exc:
            job.error, job.status = str(exc), "failed"
        except (LLMError, ValueError) as exc:
            job.error, job.status = f"The AI could not read the screenshots: {exc}", "failed"
        except Exception as exc:  # noqa: BLE001 — reported to the page instead of a silent thread death
            job.error, job.status = f"Reading the screenshots failed: {type(exc).__name__}: {exc}", "failed"

    threading.Thread(target=work, name=f"screenshots-{run_id}", daemon=True).start()
    return job.view()


@router.get("/runs/{run_ref}/screenshots/analysis/{job_id}")
def screenshot_reading_job(run_ref: str, job_id: str) -> dict[str, Any]:
    with _shot_jobs_lock:
        job = _shot_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such reading (the API may have restarted): start it again")
    return job.view()


class AskRequest(BaseModel):
    question: str
    provider: str | None = None
    max_queries: int = 8


@router.post("/runs/{run_ref}/ask")
async def ask_run(run_ref: str, body: AskRequest) -> dict[str, Any]:
    from projectstack.llm.base import LLMConfigError, LLMError
    from projectstack.llm.registry import provider_for_task
    from projectstack.results import ask as ask_mod
    from projectstack.results.sandbox import RunSandbox
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    settings = _settings()
    db = _db()
    try:
        provider = provider_for_task("ask", settings, body.provider)
        sandbox = RunSandbox(db.cfg, run_id, settings)
        result = await asyncio.to_thread(ask_mod.ask, doc, body.question, provider=provider, sandbox=sandbox,
                                         max_queries=max(1, min(body.max_queries, 12)))
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=f"{exc}. Ask needs an AI provider — add a key in Settings.") from exc
    except (LLMError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    ask_mod.save(result, folder)
    warning = ask_mod.SMALL_MODEL_WARNING if provider.name == "ollama" else None
    return {**result.as_dict(), "warning": warning}


def _run_summary(folder: Path) -> dict[str, Any] | None:
    """What the compare page shows for each run, from its findings.json (None when not on this machine)."""
    path = folder / "findings.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    return {"run": doc["run"], "verdict": doc.get("verdict"), "overall": doc.get("overall"), "steady": doc.get("steady"),
            "timeline": doc.get("timeline"), "sla": doc.get("sla"),
            "findings": [{"n": i, "type": f["type"], "severity": f["severity"], "title": f["title"],
                          "labels": f.get("labels") or []} for i, f in enumerate(doc.get("findings", []), 1)]}


def _finding_key(f: dict[str, Any]) -> str:
    """The same problem in two runs has the same type and title once the numbers are taken out."""
    title = re.sub(r"\bt\+[0-9hms]+", "", f["title"])        # offsets differ between runs
    return f"{f['type']}|{re.sub(r'[0-9][0-9.,]*', '#', title).strip()}"


def findings_diff(base: dict | None, cur: dict | None) -> dict[str, list[dict]]:
    """Findings that are new in the current run, resolved since the baseline, or still present."""
    b = {_finding_key(f): f for f in (base or {}).get("findings", []) if f["type"] != "regression"}
    c = {_finding_key(f): f for f in (cur or {}).get("findings", []) if f["type"] != "regression"}
    return {"new": [c[k] for k in c if k not in b], "resolved": [b[k] for k in b if k not in c],
            "persisting": [c[k] for k in c if k in b]}


def _comparison(base: str, current: str, regression: str | None) -> dict[str, Any]:
    from projectstack.results import store
    from projectstack.results.compare import LAST_GREEN, CompareError, RegressionThresholds, compare, find_run, load_run
    db = _db()
    runs_dir = Path(_settings()["runs_dir"])
    try:
        cur = load_run(find_run(current, runs_dir, lambda: db), runs_dir, db)
        if base.strip().lower() == LAST_GREEN:
            base_id = store.last_green(db, cur.meta.get("scenario", ""), cur.run_id)
            if not base_id:
                raise CompareError(f"No passing run of scenario {cur.meta.get('scenario')!r} before this one")
        elif base.strip().lower() == "previous":
            earlier = [r for r in store.list_runs(db, limit=500, scenario=cur.meta.get("scenario") or None)
                       if r["run_id"] < cur.run_id]
            if not earlier:
                raise CompareError(f"No earlier run of scenario {cur.meta.get('scenario')!r} to compare with")
            base_id = earlier[0]["run_id"]
        else:
            base_id = find_run(base, runs_dir, lambda: db)
        if base_id == cur.run_id:
            raise CompareError("Pick two different runs")
        comp = compare(load_run(base_id, runs_dir, db), cur, RegressionThresholds.from_settings(_settings(), regression))
    except (CompareError, ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    b_sum, c_sum = _run_summary(runs_dir / base_id), _run_summary(runs_dir / cur.run_id)
    return {**comp, "runs": {"baseline": b_sum, "current": c_sum}, "findings_diff": findings_diff(b_sum, c_sum)}


@router.get("/compare")
def compare_runs(base: str, current: str, regression: str | None = None) -> dict[str, Any]:
    """Compare run `current` with `base` (a run id, `last-green` or `previous`)."""
    return _comparison(base, current, regression)


@router.get("/compare/report")
def compare_report(base: str, current: str, regression: str | None = None):
    """The comparison as Markdown (for a ticket, a merge request or an email)."""
    from fastapi.responses import Response
    from projectstack.results.ci import comparison_markdown
    comp = _comparison(base, current, regression)
    text = comparison_markdown(comp, limit=None)
    diff = comp["findings_diff"]
    if any(diff.values()):
        text += "\n### Findings\n\n"
        for title, key in (("New in this run", "new"), ("Resolved since the baseline", "resolved"), ("Still present", "persisting")):
            if diff[key]:
                text += f"**{title}**\n\n" + "".join(f"- {f['severity']}: {f['title']}\n" for f in diff[key]) + "\n"
    name = f"compare_{comp['baseline']['run_id']}_vs_{comp['current']['run_id']}.md"
    return Response(text, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/runs/{run_ref}")
def delete_run(run_ref: str) -> dict[str, Any]:
    from projectstack.results import store
    run_id, folder = _resolve(run_ref)
    store.delete_run(_db(), run_id, folder.parent)
    return {"deleted": run_id}


def wait_for(job: Job, timeout: float = 60.0) -> Job:
    """Block until a job finishes (tests)."""
    deadline = time.monotonic() + timeout
    while job.status in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.1)
    return job
