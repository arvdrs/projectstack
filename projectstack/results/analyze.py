"""
`pstack analyze`: ingest a JTL into ClickHouse, register the run, compute its
metrics and write the run's artifacts.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from projectstack.config import load_settings
from projectstack.results import metrics as m
from projectstack.results import store
from projectstack.results.db import ResultsDB, connect
from projectstack.results.ingest import IngestResult, classify_labels, ingest, present_columns, sniff
from projectstack.results import findings as findings_doc
from projectstack.results.compare import (
    CompareError, RegressionThresholds, RunData, baseline_section, compare, describe_missing_baseline, load_run,
    regression_findings, resolve_baseline,
)
from projectstack.results.detectors import Context, Thresholds, run_all
from projectstack.results.sla_spec import parse_sla
from projectstack.results.steady import detect_for_run
from projectstack.results.window import parse_steady

STATS_MODES = ("both", "parent", "child")


@dataclass
class Analysis:
    run_id: str
    meta: dict[str, Any]
    metrics: dict[str, Any]
    findings: dict[str, Any]
    ingest: IngestResult
    folder: Path
    timings: dict[str, float]
    comparison: dict[str, Any] | None = None


def analyze_file(
    path: str | Path,
    *,
    steady: str | None = None,
    sla: str | None = None,
    tag: str = "",
    scenario: str | None = None,
    props: str | Path | None = None,
    stats: str = "both",
    max_bad_rows: int = 0,
    baseline: str | None = None,
    regression: str | None = None,
    jmx: str | Path | None = None,
    settings: dict[str, str] | None = None,
    clickhouse_url: str | None = None,
    db: ResultsDB | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> Analysis:
    path = Path(path).resolve()
    settings = settings or load_settings()
    if stats not in STATS_MODES:
        raise ValueError(f"--stats must be one of {', '.join(STATS_MODES)}")
    sla_config = parse_sla(sla)          # validate before any work
    thresholds = RegressionThresholds.from_settings(settings, regression)
    plan = None
    if jmx:
        from projectstack.results.plan import PlanError, load_plan
        try:
            plan = load_plan(jmx)
        except PlanError as exc:
            raise ValueError(f"--jmx: {exc}") from exc
    timings: dict[str, float] = {}

    t0 = time.monotonic()
    info = sniff(path, props)
    db = db or connect(settings, clickhouse_url)
    scenario = scenario or path.name.split(".")[0]
    base_id, base_note = None, ""
    if baseline:
        try:
            base_id = resolve_baseline(baseline, db=db, scenario=scenario)
        except LookupError as exc:
            raise ValueError(f"--baseline: {exc}") from exc
        if base_id is None:
            base_note = describe_missing_baseline(scenario)
    run_id = store.new_run_id(db, tag)
    meta: dict[str, Any] = {
        "schema": "projectstack.run/1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "scenario": scenario,
        "tag": tag,
        "source": {"file": str(path), "format": info.fmt, "gzipped": info.gzipped,
                   "size_bytes": path.stat().st_size, "column_source": info.column_source},
        "clickhouse": db.cfg.display,
        "sla": sla_config,
        "stats_mode": stats,
        **({"plan": {"file": str(Path(jmx).resolve())}} if jmx else {}),
    }
    store.save_run(db, run_id, status="ingesting", meta=meta)

    try:
        progress(f"Loading {path.name} into ClickHouse ({db.cfg.display}) …")
        result = ingest(db, run_id, info, max_bad_rows=max_bad_rows)
        timings["ingest_s"] = result.seconds

        t1 = time.monotonic()
        kinds = classify_labels(db, run_id)
        bounds = m.run_bounds(db, run_id)
        present = present_columns(result.info)
        users_from_threads = "allThreads" not in present
        if steady:
            window = parse_steady(steady, bounds["start_ms"], bounds["last_ts_ms"])
            steady_info = {**window.as_dict(), "method": "--steady", "note": f"Window given with --steady {steady}"}
        else:
            detected = detect_for_run(db, run_id, bounds, users_from_threads)
            window, steady_info = detected.window, detected.as_dict()
        progress("Computing metrics …")
        metrics = m.compute(db, run_id, window, kinds, bounds, users_from_threads)
        timings["metrics_s"] = time.monotonic() - t1

        t2 = time.monotonic()
        progress("Running detectors …")
        ctx = Context(db=db, run_id=run_id, bounds=bounds, kinds=kinds, metrics=metrics, steady=steady_info,
                      columns=present, sla=sla_config, thresholds=Thresholds.from_settings(settings),
                      extra={"plan": plan} if plan else {})
        found, skipped = run_all(ctx)
        comparison = None
        if base_id:
            progress(f"Comparing with baseline {base_id} …")
            current = RunData(run_id, {"scenario": meta["scenario"], "tag": tag, "created_at": meta["created_at"],
                                       "steady": steady_info}, metrics)
            try:
                comparison = compare(load_run(base_id, Path(settings["runs_dir"]), db), current, thresholds)
            except CompareError as exc:
                base_note = f"Baseline {base_id} could not be loaded: {exc}"
                progress(f"Warning: {base_note}")
            if comparison:
                found += regression_findings(comparison, window.as_dict())
                order = {"critical": 0, "warning": 1, "info": 2}
                found.sort(key=lambda f: (order.get(f.severity, 9), f.start_ms or 0))
        timings["detectors_s"] = time.monotonic() - t2
        timings["total_s"] = time.monotonic() - t0

        peak_users = max((pt["users"] for pt in metrics["timeline"]["points"]), default=0)
        meta.update({
            "run": {**bounds, "duration_s": round((bounds["end_ms"] - bounds["start_ms"]) / 1000, 3),
                    "peak_users": peak_users},
            "columns": {"present": present, "users_source": metrics["timeline"]["users_source"]},
            "labels": {k: sum(1 for v in kinds.values() if v == k) for k in ("request", "transaction", "subresult")},
            "window": window.as_dict(),
            "steady": steady_info,
            "ingest": {"rows": result.rows, "seconds": round(result.seconds, 3), "warnings": result.warnings},
            "timings": {k: round(v, 3) for k, v in timings.items()},
        })
        doc = findings_doc.build(meta, metrics, steady_info, found, skipped, ctx.extra.get("sla_result", {"defined": False}),
                                 baseline_section(comparison, baseline, base_note))
        meta["verdict"] = doc["verdict"]
        if baseline:
            meta["baseline"] = {"requested": baseline, "run_id": base_id,
                                "gating": bool(comparison and comparison["gating"])}
        if comparison:
            comparison["current"]["verdict"] = doc["verdict"]
        meta["findings"] = {sev: sum(1 for f in found if f.severity == sev) for sev in ("critical", "warning", "info")}
        summary = metrics["overall"].get("request") or metrics["overall"].get("transaction") or {}
        store.save_run(db, run_id, status="ready", meta=meta, summary=summary, verdict=doc["verdict"])
        artifacts = {"meta": meta, "metrics": metrics, "findings": doc}
        if comparison:
            artifacts["comparison"] = comparison
        folder = store.write_artifacts(Path(settings["runs_dir"]), run_id, **artifacts)
        if jmx:
            shutil.copy2(jmx, folder / "plan.jmx")     # so `pstack health` can map findings later
    except BaseException:
        # Leave nothing half-loaded behind
        try:
            store.delete_run(db, run_id)
        except Exception:
            pass
        raise
    return Analysis(run_id, meta, metrics, doc, result, folder, timings, comparison)
