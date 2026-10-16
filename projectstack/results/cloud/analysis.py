"""
Server metrics in the analysis: store them, summarise them over the run, turn them
into findings, and connect them with what the test saw.

Server findings (type "server") are deterministic like every other detector:

  hot           CPU, or a service limit (DTU, sessions, workers), at 85% or more for 3+ minutes
  credits       CPU credits of a burstable instance ran out (it is then throttled)
  memory        memory in use at 90% or more for 3+ minutes, or growing steadily (a possible leak);
                available memory dropping by a third or more
  errors        5xx / failed connections / deadlocks / failed status checks / rejected connections
  throttles     throttled calls (Lambda)
  evictions     cache evictions (memory full)
  queue         work queuing (disk queue, HTTP queue) for 3+ minutes
  latency       server-side latency stepped up by half or more
  connections   connections held flat at their peak (a pool or server limit)
  scaling       instances / tasks / replicas changed during the run

A server finding that starts within two minutes of a test finding (a latency change,
saturation, an error burst …) is linked to it both ways, so reports and the AI can say
"saturation at 5,473 users coincides with RDS CPU at 97%".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from projectstack.results.cloud import FetchResult, Resource, Series
from projectstack.results.cloud.presets import resource_type

HOT_PCT = 85.0
MEM_PCT = 90.0
SUSTAIN_S = 180
LINK_S = 120
SERVER_MARK = " At the same time on the servers: "
TEST_LINKABLE = ("latency_change", "saturation", "error_burst", "error_rate_change", "injector_connect",
                 "injector_throughput", "sla_breach")


def _run_start_ms(doc: dict[str, Any]) -> int:
    return int(datetime.fromisoformat(doc["run"]["start"]).timestamp() * 1000)


def _fmt(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.0f}%"
    if unit == "ms":
        return f"{v:,.0f} ms"
    if unit == "bytes":
        for size, name in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
            if abs(v) >= size:
                return f"{v / size:,.1f} {name}"
        return f"{v:,.0f} B"
    if unit == "bytes/s":
        return f"{_fmt(v, 'bytes')}/s"
    if unit in ("/min", "/s"):
        return f"{v:,.1f}{unit}"
    if unit == "cores":
        return f"{v:,.2f} cores"
    return f"{v:,.0f}" if abs(v) >= 10 else f"{v:,.2f}".rstrip("0").rstrip(".")


def _offset(ms: int, start_ms: int) -> str:
    from projectstack.results.fmt import offset
    return offset((ms - start_ms) / 1000)


def _streaks(points: list[tuple[int, float]], test, period: int) -> list[tuple[int, int, float]]:
    """(start ms, end ms, peak) of consecutive points that pass `test`, lasting at least SUSTAIN_S."""
    out, cur = [], []
    for t, v in points + [(None, None)]:
        if t is not None and test(v):
            cur.append((t, v))
            continue
        if cur and (len(cur) * period) >= SUSTAIN_S:
            out.append((cur[0][0], cur[-1][0] + period * 1000, max(v for _, v in cur)))
        cur = []
    return out


def _thirds(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 6:
        return None
    k = len(values) // 3
    return float(np.median(values[:k])), float(np.median(values[-k:]))


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect(series: list[Series], doc: dict[str, Any]) -> list[dict[str, Any]]:
    start_ms = _run_start_ms(doc)
    end_ms = start_ms + int(float(doc["run"]["duration_s"]) * 1000)
    st = doc.get("steady") or {}
    ws, we = int(st.get("start_ms", start_ms)), int(st.get("end_ms", end_ms))
    out: list[dict[str, Any]] = []

    def finding(s: Series, pattern: str, severity: str, title: str, detail: str, a: int, b: int, **ev: Any) -> None:
        r, m = s.resource, s.metric
        run_pts = [v for t, v in s.points if start_ms <= t <= end_ms]
        out.append({
            "type": "server", "severity": severity, "title": title, "detail": detail,
            "start_ms": a, "end_ms": b, "labels": [],
            "start_offset_s": round((a - start_ms) / 1000), "end_offset_s": round((b - start_ms) / 1000),
            "evidence": {"pattern": pattern, "provider": r.provider, "resource": r.display, "role": r.role,
                         "resource_type": resource_type(r.provider, r.type).title, "metric": m.label, "unit": m.unit,
                         "max": round(max(run_pts), 2) if run_pts else None,
                         "avg": round(sum(run_pts) / len(run_pts), 2) if run_pts else None, **ev},
        })

    for s in series:
        r, m, period = s.resource, s.metric, s.period_s
        pts = [(t, v) for t, v in s.points if start_ms - period * 1000 <= t <= end_ms]
        if not pts:
            continue
        values = [v for _, v in pts]
        steady_vals = [v for t, v in pts if ws <= t < we]
        where = f"{r.display} ({resource_type(r.provider, r.type).title})"
        k = m.kind

        if k in ("cpu_pct", "limit_pct"):
            for a, b, peak in _streaks(pts, lambda v: v >= HOT_PCT, period):
                long = (b - a) >= 0.5 * max(we - ws, 1)
                what = "CPU" if k == "cpu_pct" else f"{m.label} (share of the service limit)"
                finding(s, "hot", "critical" if peak >= 95 or long else "warning",
                        f"{m.label} at {peak:.0f}% on {r.display}",
                        f"{what} on {where} stayed at {HOT_PCT:.0f}% or more for {(b - a) // 60000} min "
                        f"({_offset(a, start_ms)} to {_offset(b, start_ms)}), peaking at {peak:.0f}%. "
                        + ("Requests queue for processor time at this level, so response times rise with load."
                           if k == "cpu_pct" else "Beyond the limit, work queues or is rejected."),
                        a, b, peak=round(peak, 1))
        elif k == "credits":
            top = max(values)
            low = [(t, v) for t, v in pts if v <= max(top * 0.05, 0.5)]
            if top > 0 and low and values[-1] < values[0]:
                a = low[0][0]
                finding(s, "credits", "critical", f"CPU credits ran out on {r.display} at {_offset(a, start_ms)}",
                        f"{where} used up its CPU credit balance (from {_fmt(top, '')} to {_fmt(min(values), '')}). "
                        "A burstable instance is then held to its baseline CPU, which slows everything it serves.",
                        a, end_ms, min=round(min(values), 2))
        elif k == "mem_pct":
            for a, b, peak in _streaks(pts, lambda v: v >= MEM_PCT, period):
                finding(s, "memory", "critical" if peak >= 95 else "warning",
                        f"Memory at {peak:.0f}% on {r.display}",
                        f"Memory in use on {where} stayed at {MEM_PCT:.0f}% or more for {(b - a) // 60000} min, peaking at "
                        f"{peak:.0f}%; the system may swap, collect garbage often or start failing allocations.",
                        a, b, peak=round(peak, 1))
            th = _thirds(steady_vals)
            if th and th[1] - th[0] >= 15:
                finding(s, "memory_growth", "warning", f"Memory kept growing on {r.display}",
                        f"Memory in use on {where} rose from about {th[0]:.0f}% to {th[1]:.0f}% during the steady state "
                        "while the load stayed level — a possible memory leak.", ws, we,
                        before=round(th[0], 1), after=round(th[1], 1))
        elif k == "mem_free":
            th = _thirds(steady_vals)
            if th and th[0] > 0 and th[1] <= 0.67 * th[0]:
                finding(s, "memory_growth", "warning", f"Available memory fell on {r.display}",
                        f"{m.label} on {where} dropped from about {_fmt(th[0], m.unit)} to {_fmt(th[1], m.unit)} during the "
                        "steady state while the load stayed level.", ws, we,
                        before=round(th[0]), after=round(th[1]))
        elif k in ("errors", "throttles", "evictions"):
            per_min = m.unit in ("/min", "/s")
            total = sum(v * (period / 60 if m.unit == "/min" else period if m.unit == "/s" else 1) for v in values) \
                if per_min else max(values)
            if total >= 1:
                hit = [t for t, v in pts if v > 0]
                a, b = hit[0], hit[-1] + period * 1000
                if k == "throttles":
                    sev, why = "critical", "Calls beyond the account or function concurrency limit were throttled."
                elif k == "evictions":
                    sev, why = "warning", "The cache ran out of memory and dropped keys, so more requests go to the database."
                else:
                    sev, why = "warning", ""
                amount = f"{total:,.0f}" if per_min else f"up to {_fmt(total, m.unit)}"
                finding(s, k, sev, f"{m.label}: {amount} on {r.display}",
                        f"{where} reported {m.label.lower()} ({amount}) between {_offset(a, start_ms)} and "
                        f"{_offset(b, start_ms)}. {why}".strip(), a, b, total=round(total, 2))
        elif k == "queue":
            limit = 10.0 if m.key == "queue" and r.provider == "aws" else 1.0
            for a, b, peak in _streaks(pts, lambda v: v >= limit, period):
                finding(s, "queue", "warning", f"{m.label} reached {_fmt(peak, m.unit)} on {r.display}",
                        f"Work queued on {where} for {(b - a) // 60000} min ({_offset(a, start_ms)} to "
                        f"{_offset(b, start_ms)}), peaking at {_fmt(peak, m.unit)}.", a, b, peak=round(peak, 2))
        elif k == "latency" and len(values) >= 6:
            from projectstack.results.detectors.changepoint import change_points, segment_levels
            segs = segment_levels(values, change_points(values))
            for (s0, e0, before), (s1, e1, after) in zip(segs, segs[1:]):
                if after >= 1.5 * before and after - before >= max(5.0, 0.1 * before):
                    a = pts[s1][0]
                    finding(s, "latency", "warning",
                            f"{m.label} on {r.display} rose from {_fmt(before, 'ms')} to {_fmt(after, 'ms')} at {_offset(a, start_ms)}",
                            f"Server-side {m.label.lower()} on {where} stepped up {after / max(before, 1e-9):.1f}× at "
                            f"{_offset(a, start_ms)}.", a, pts[e1 - 1][0] + period * 1000,
                            before=round(before, 1), after=round(after, 1))
        elif k == "connections":
            top = max(values)
            if top >= 10:
                flat = _streaks(pts, lambda v: v >= 0.98 * top, period)
                longest = max(flat, key=lambda x: x[1] - x[0], default=None)
                if longest and (longest[1] - longest[0]) >= 5 * 60000:
                    a, b, _ = longest
                    finding(s, "connections", "info", f"{m.label} held at {_fmt(top, '')} on {r.display}",
                            f"{m.label} on {where} stayed at their peak of about {_fmt(top, '')} for "
                            f"{(b - a) // 60000} min from {_offset(a, start_ms)} — often a connection-pool or server "
                            "limit, which makes requests wait for a free connection.", a, b, peak=round(top, 1))
        elif k == "capacity":
            run_vals = [v for t, v in pts if start_ms <= t <= end_ms] or values
            if max(run_vals) - min(run_vals) >= 1:
                first = run_vals[0]
                change = next(((t, v) for t, v in pts if abs(v - first) >= 1), None)
                a = change[0] if change else pts[0][0]
                finding(s, "scaling", "info", f"{m.label} on {r.display} went from {first:.0f} to {run_vals[-1]:.0f}",
                        f"Autoscaling changed {m.label.lower()} on {where} during the run (first change at "
                        f"{_offset(a, start_ms)}; between {min(run_vals):.0f} and {max(run_vals):.0f}). Statistics "
                        "before and after the change are not directly comparable.", a, end_ms,
                        low=round(min(run_vals)), high=round(max(run_vals)))
        elif k == "health_pct":
            low = min(values)
            if low < 100:
                hit = [t for t, v in pts if v < 100]
                finding(s, "health", "warning", f"{m.label} dropped to {low:.0f}% on {r.display}",
                        f"Health probes failed for part of the backend pool of {where}.", hit[0], hit[-1] + period * 1000,
                        min=round(low, 1))
    return out


# ---------------------------------------------------------------------------
# Summary and links with the test's findings
# ---------------------------------------------------------------------------

def summarise(series: list[Series], doc: dict[str, Any], result: FetchResult, resources: list[Resource]) -> dict[str, Any]:
    start_ms = _run_start_ms(doc)
    end_ms = start_ms + int(float(doc["run"]["duration_s"]) * 1000)
    st = doc.get("steady") or {}
    ws, we = int(st.get("start_ms", start_ms)), int(st.get("end_ms", end_ms))
    by_res: dict[str, dict[str, Any]] = {}
    for r in resources:
        by_res[f"{r.provider}:{r.id}"] = {"name": r.display, "provider": r.provider,
                                          "type": resource_type(r.provider, r.type).title, "role": r.role, "metrics": []}
    for s in series:
        run_pts = [(t, v) for t, v in s.points if start_ms <= t <= end_ms]
        steady = [v for t, v in run_pts if ws <= t < we]
        if not run_pts:
            continue
        peak_t, peak_v = max(run_pts, key=lambda x: x[1])
        entry = by_res.setdefault(f"{s.resource.provider}:{s.resource.id}", {
            "name": s.resource.display, "provider": s.resource.provider, "role": s.resource.role,
            "type": resource_type(s.resource.provider, s.resource.type).title, "metrics": []})
        entry["metrics"].append({
            "metric": s.metric.key, "label": s.metric.label, "unit": s.metric.unit,
            "steady_avg": round(sum(steady) / len(steady), 2) if steady else None,
            "max": round(peak_v, 2), "max_offset_s": round((peak_t - start_ms) / 1000)})
    return {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "period_s": min((s.period_s for s in series), default=60),
            "resources": list(by_res.values())[:20],
            "missing": result.missing[:12], "warnings": result.warnings[:6]}


def _strip_links(f: dict[str, Any]) -> dict[str, Any]:
    f = dict(f)
    if SERVER_MARK in f.get("detail", ""):
        f["detail"] = f["detail"].split(SERVER_MARK, 1)[0]
    f["evidence"] = {k: v for k, v in (f.get("evidence") or {}).items() if k != "server"}
    return f


def link(test_findings: list[dict[str, Any]], server_findings: list[dict[str, Any]]) -> None:
    """Connect server findings with test findings that start within LINK_S of each other (in place)."""
    for tf in test_findings:
        if tf["type"] not in TEST_LINKABLE or tf.get("start_offset_s") is None:
            continue
        t = tf["start_offset_s"]
        near = []
        for sf in server_findings:
            a, b = sf["start_offset_s"], sf["end_offset_s"]
            if tf["type"] == "sla_breach":
                # Measured over the whole steady state: a serious server problem during it is context
                if (sf["severity"] in ("critical", "warning") and a <= tf.get("end_offset_s", t) and b >= t
                        and sf["evidence"]["pattern"] in ("hot", "credits", "memory", "throttles", "errors")):
                    near.append(sf)
            elif abs(a - t) <= LINK_S or a <= t <= b:
                near.append(sf)
        if not near:
            continue
        titles = [sf["title"] for sf in near[:3]]
        tf["evidence"] = {**(tf.get("evidence") or {}), "server": titles}
        tf["detail"] = tf["detail"].rstrip() + SERVER_MARK + "; ".join(titles) + "."
        for sf in near:
            sf["evidence"].setdefault("coincides_with", []).append(tf["title"])
            if tf["severity"] == "critical":
                sf["_with_critical"] = True
    bump = {"info": "warning", "warning": "critical", "critical": "critical"}
    for sf in server_findings:
        # Coinciding with a critical slowdown raises a server finding one level (once), scaling excepted
        if sf.pop("_with_critical", False) and sf["evidence"]["pattern"] != "scaling":
            sf["severity"] = bump[sf["severity"]]
    for sf in server_findings:
        if sf["evidence"].get("coincides_with"):
            sf["detail"] = sf["detail"].rstrip() + " It coincides with: " + "; ".join(sf["evidence"]["coincides_with"][:2]) + "."


def recompute_verdict(doc: dict[str, Any]) -> str:
    sla = doc.get("sla") or {}
    if not sla.get("defined"):
        return "no_sla"
    if not sla.get("passed"):
        return "fail"
    if any(f["type"] != "sla_breach" and f["severity"] in ("critical", "warning") for f in doc.get("findings", [])):
        return "at_risk"
    return "pass"


# ---------------------------------------------------------------------------
# Attaching fetched metrics to an analysed run
# ---------------------------------------------------------------------------

def store(db, run_id: str, series: list[Series]) -> int:
    db.command(f"DELETE FROM {db.db}.server_metrics WHERE run_id = {{r:String}}", {"r": run_id})
    rows = []
    for s in series:
        for t, v in s.points:
            rows.append([run_id, s.resource.provider, s.resource.type, s.resource.id, s.resource.display, s.resource.role,
                         s.metric.key, s.metric.label, s.metric.kind, s.metric.unit, s.period_s,
                         datetime.fromtimestamp(t / 1000, tz=timezone.utc), float(v)])
    if rows:
        db.insert("server_metrics", rows, ["run_id", "provider", "resource_type", "resource_id", "resource_name", "role",
                                           "metric", "label", "kind", "unit", "period_s", "ts", "value"])
    return len(rows)


def attach(db, folder: Path, result: FetchResult, resources: list[Resource]) -> dict[str, Any]:
    """Store the metrics, add server findings and the server summary to findings.json, update the verdict."""
    from projectstack.results import store as run_store
    doc = json.loads((folder / "findings.json").read_text())
    meta_path = folder / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    run_id = doc["run"]["run_id"]
    start_ms = _run_start_ms(doc)
    rows = store(db, run_id, result.series)
    (folder / "server.json").write_text(json.dumps({
        "schema": "projectstack.server/1", "run_id": run_id, "run_start_ms": start_ms,
        "resources": [r.__dict__ for r in resources],
        "series": [s.as_dict(start_ms) for s in result.series],
        "missing": result.missing, "warnings": result.warnings,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1))

    server_findings = detect(result.series, doc)
    test_findings = [_strip_links(f) for f in doc.get("findings", []) if f["type"] != "server"]
    link(test_findings, server_findings)
    order = {"critical": 0, "warning": 1, "info": 2}
    doc["findings"] = sorted(test_findings + server_findings,
                             key=lambda f: (order.get(f["severity"], 9), f.get("start_ms") or 0))
    doc["server"] = summarise(result.series, doc, result, resources)
    doc["verdict"] = recompute_verdict(doc)
    doc["size_bytes"] = len(json.dumps(doc, separators=(",", ":"), default=str).encode())
    (folder / "findings.json").write_text(json.dumps(doc, indent=2, default=str))
    if meta is not None:
        meta["verdict"] = doc["verdict"]
        meta["server"] = {"fetched_at": doc["server"]["fetched_at"], "resources": len(resources), "rows": rows}
        meta["findings"] = {sev: sum(1 for f in doc["findings"] if f["severity"] == sev) for sev in ("critical", "warning", "info")}
        meta_path.write_text(json.dumps(meta, indent=2, default=str))
        try:
            run_store.save_run(db, run_id, status="ready", meta=meta, verdict=doc["verdict"],
                               summary=(doc.get("overall") or {}).get("request") or (doc.get("overall") or {}).get("transaction"))
        except Exception:
            pass
    return {"run_id": run_id, "series": len(result.series), "rows": rows, "findings": len(server_findings),
            "verdict": doc["verdict"], "missing": result.missing, "warnings": result.warnings}


def read(folder: Path) -> dict[str, Any] | None:
    path = folder / "server.json"
    return json.loads(path.read_text()) if path.exists() else None



def fetch_and_attach(db, folder: Path, settings: dict[str, str], resources: list[Resource] | None = None) -> dict[str, Any]:
    """Fetch the run's window for `resources` (default: those saved for its scenario) and attach them."""
    from projectstack.results.cloud import CloudError, fetch, load_targets
    doc = json.loads((folder / "findings.json").read_text())
    if resources is None:
        resources = load_targets(settings, doc["run"].get("scenario", ""))
    if not resources:
        raise CloudError(f"No cloud resources are saved for scenario {doc['run'].get('scenario')!r} — pick some first")
    start_ms = _run_start_ms(doc)
    end_ms = start_ms + int(float(doc["run"]["duration_s"]) * 1000)
    result = fetch(resources, start_ms, end_ms, settings)
    if not result.series:
        why = "; ".join(result.warnings[:3]) or "none of the metrics had data for this window"
        raise CloudError(f"No server metrics came back ({why})")
    return attach(db, folder, result, resources)
