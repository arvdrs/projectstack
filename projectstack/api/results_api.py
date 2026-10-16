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

from projectstack.config import load_settings

router = APIRouter(prefix="/api/results", tags=["results"])

UPLOAD_DIR = Path.home() / ".projectstack" / "uploads"
CLICKHOUSE_HINT = "docker compose -f deploy/clickhouse/docker-compose.yml up -d"


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
    return json.loads(path.read_text())


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


def observations(doc: dict[str, Any]) -> dict[str, Any]:
    """High-level observations in plain language (rule-based, so always available and free)."""
    from projectstack.results.narrative import from_rules
    return from_rules(doc, "exec").content


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
        "folder": str(folder),
    }


class AiRequest(BaseModel):
    provider: str | None = None
    fresh: bool = False


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
                     format: str = Query("html", pattern="^(html|md)$"), ai: bool = False,
                     download: bool = False):
    """The technical or executive report. Without ai, the rule-based narrative (nothing leaves the machine)."""
    from projectstack.results.report import ReportError, build_report
    run_id, folder = _resolve(run_ref)
    # Kept apart from the CLI's report_<audience>.<format> so the web app never overwrites those
    out = folder / "web" / f"report_{audience}{'_ai' if ai else ''}.{format}"
    try:
        await asyncio.to_thread(build_report, str(folder), audience=audience, formats=(format,), use_llm=ai,
                                out=str(out), settings=_settings())
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if format == "html" and not download:
        return HTMLResponse(out.read_text(encoding="utf-8"))
    media = "text/html" if format == "html" else "text/markdown"
    return FileResponse(out, media_type=media, filename=f"{run_id}_{audience}.{format}")


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


@router.get("/runs/{run_ref}/labels-report")
async def labels_report(run_ref: str, format: str = Query("html", pattern="^(html|md)$"),
                        limit: int = Query(40, ge=1, le=500), download: bool = False):
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
        return HTMLResponse(text)
    return FileResponse(out, media_type="text/html" if format == "html" else "text/markdown",
                        filename=f"{run_id}_requests_transactions.{format}")


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
    from projectstack.results.cloud.analysis import read
    run_id, folder = _resolve(run_ref)
    doc = _findings(folder)
    data = read(folder)
    return {"run_id": run_id, "scenario": doc["run"].get("scenario", ""), "available": data is not None,
            "summary": doc.get("server"), "series": (data or {}).get("series", []),
            "resources": (data or {}).get("resources", []), "missing": (data or {}).get("missing", []),
            "warnings": (data or {}).get("warnings", []), "fetched_at": (data or {}).get("fetched_at"),
            "steady": doc.get("steady"), "duration_s": doc["run"]["duration_s"],
            "findings": [{**f, "n": i} for i, f in enumerate(doc.get("findings", []), 1) if f["type"] == "server"]}


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
