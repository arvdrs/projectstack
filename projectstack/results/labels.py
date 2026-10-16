"""
Per-label analysis: everything about one request or transaction in a run, and a
plain-language synthesis of it.

For one label: steady-state and whole-run statistics, an exact percentile ladder,
the response-time distribution, its own timeline, response codes, top errors, the
slowest samples, where its time goes (connect / time to first byte / download),
its share of the run, the findings that name it, its SLA check and how it compares
with the baseline run. Every query filters on the label, which leads the samples
table's sort key, so this stays fast on runs with millions of samples.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from projectstack.results.db import ResultsDB
from projectstack.results.fmt import ms, num, offset, pct_text
from projectstack.results.metrics import _STAT_COLS, _stats

LADDER = (0.5, 0.75, 0.9, 0.95, 0.99, 0.999)


def _window(doc: dict[str, Any]) -> tuple[int, int, float]:
    st = doc.get("steady") or {}
    return int(st["start_ms"]), int(st["end_ms"]), float(st.get("duration_s") or 0)


def label_kinds(db: ResultsDB, run_id: str) -> dict[str, str]:
    return {label: kind for label, kind in db.rows(
        f"SELECT label, toString(kind) FROM {db.db}.run_labels FINAL WHERE run_id = {{r:String}}", {"r": run_id})}


def kind_totals(db: ResultsDB, run_id: str, doc: dict[str, Any]) -> dict[str, float]:
    """Total response time per label kind in the steady state (for each label's share), in one scan."""
    ws, we, _ = _window(doc)
    rows = db.rows(
        f"""SELECT toString(l.kind), sum(s.elapsed) FROM {db.db}.samples AS s
            INNER JOIN (SELECT label, kind FROM {db.db}.run_labels FINAL WHERE run_id = {{run:String}}) AS l USING label
            WHERE s.run_id = {{run:String}} AND s.ts >= fromUnixTimestamp64Milli({{ws:Int64}})
              AND s.ts < fromUnixTimestamp64Milli({{we:Int64}}) GROUP BY l.kind""",
        {"run": run_id, "ws": ws, "we": we})
    return {k: float(v or 0) for k, v in rows}


def detail(db: ResultsDB, run_id: str, label: str, doc: dict[str, Any], baseline_metrics: dict | None = None,
           baseline_info: dict | None = None, totals: dict[str, float] | None = None,
           kinds: dict[str, str] | None = None) -> dict[str, Any]:
    """Everything about one label (see the module docstring)."""
    kinds = kinds or label_kinds(db, run_id)
    if label not in kinds:
        raise LookupError(f"No label {label!r} in run {run_id}")
    kind = kinds[label]
    ws, we, dur = _window(doc)
    start_ms = int(_parse_start(doc))
    end_ms = start_ms + int(float(doc["run"]["duration_s"]) * 1000)
    p = {"run": run_id, "label": label, "ws": ws, "we": we}
    is_label = "run_id = {run:String} AND label = {label:String}"
    in_window = f"{is_label} AND ts >= fromUnixTimestamp64Milli({{ws:Int64}}) AND ts < fromUnixTimestamp64Milli({{we:Int64}})"

    steady = _stats(db.dicts(f"SELECT {_STAT_COLS} FROM {db.db}.samples WHERE {in_window}", p)[0], dur)
    full_dur = max((end_ms - start_ms) / 1000, 0.001)
    whole = _stats(db.dicts(f"SELECT {_STAT_COLS} FROM {db.db}.samples WHERE {is_label}", p)[0], full_dur)

    extra = db.dicts(
        f"""SELECT quantilesExact({', '.join(map(str, LADDER))})(elapsed) AS ladder,
                   avg(latency) AS latency, avg(connect) AS connect, avg(bytes) AS bytes, avg(sent_bytes) AS sent,
                   sum(elapsed) AS total_ms, stddevPop(elapsed) AS sd
            FROM {db.db}.samples WHERE {in_window}""", p)[0]
    if totals is None:
        totals = kind_totals(db, run_id, doc)
    kind_total = totals.get(kind, 0.0)
    ladder = [{"p": q, "ms": int(v)} for q, v in zip(LADDER, extra["ladder"])] if steady["samples"] else []

    run_s = (end_ms - start_ms) / 1000
    bucket = 60 if run_s >= 600 else 10
    timeline = [
        {"t_offset_s": round((int(t) - start_ms) / 1000), "samples": int(n), "errors": int(e),
         "tps": round(int(n) / bucket, 3), "p50_ms": int(q[0]), "p95_ms": int(q[1]), "p99_ms": int(q[2]),
         "avg_ms": round(float(a), 1), "error_pct": round(int(e) / int(n) * 100, 2) if n else 0.0}
        for t, n, e, q, a in db.rows(
            f"""SELECT toUnixTimestamp(toStartOfInterval(ts, toIntervalSecond({bucket}))) * 1000 AS t, count(),
                       countIf(NOT success), quantilesExact(0.5, 0.95, 0.99)(elapsed), avg(elapsed)
                FROM {db.db}.samples WHERE {is_label} GROUP BY t ORDER BY t""", p)]

    histogram = _histogram(db, p, in_window, ladder)
    codes = [{"code": c or "(none)", "samples": int(n), "failed": int(f), "share_pct": round(int(n) / whole["samples"] * 100, 2)}
             for c, n, f in db.rows(f"""SELECT response_code, count() AS n, countIf(NOT success) FROM {db.db}.samples
                                       WHERE {is_label} GROUP BY response_code ORDER BY n DESC LIMIT 12""", p)] \
        if whole["samples"] else []
    errors = [{"code": c, "message": " ".join(str(m).split())[:200], "count": int(n),
               "first_offset_s": round((int(a) - start_ms) / 1000), "last_offset_s": round((int(b) - start_ms) / 1000)}
              for c, m, n, a, b in db.rows(
                  f"""SELECT response_code,
                             if(failure_message != '', failure_message,
                                if(startsWith(response_message, 'Number of samples in transaction'), '', response_message)) AS msg,
                             count() AS n,
                             toUnixTimestamp64Milli(min(ts)), toUnixTimestamp64Milli(max(ts))
                      FROM {db.db}.samples WHERE {is_label} AND NOT success GROUP BY response_code, msg
                      ORDER BY n DESC LIMIT 8""", p)]
    slowest = [{"t_offset_s": round((int(t) - start_ms) / 1000), "elapsed_ms": int(e), "latency_ms": int(lat),
                "code": c, "success": bool(s), "thread": th}
               for t, e, lat, c, s, th in db.rows(
                   f"""SELECT toUnixTimestamp64Milli(ts), elapsed, latency, response_code, success, thread_name
                       FROM {db.db}.samples WHERE {in_window} ORDER BY elapsed DESC LIMIT 10""", p)]

    breakdown = None
    if kind == "request" and steady["samples"] and float(extra["latency"] or 0) > 0:
        conn, ttfb, avg = float(extra["connect"] or 0), float(extra["latency"] or 0), steady["avg_ms"]
        breakdown = {"connect_ms": round(conn, 1), "server_ms": round(max(ttfb - conn, 0), 1),
                     "download_ms": round(max(avg - ttfb, 0), 1), "avg_ms": avg,
                     "bytes": round(float(extra["bytes"] or 0)), "sent_bytes": round(float(extra["sent"] or 0))}

    findings = [{"n": i, "type": f["type"], "severity": f["severity"], "title": f["title"]}
                for i, f in enumerate(doc.get("findings", []), 1) if _names(f, label)]
    sla = [c for c in (doc.get("sla") or {}).get("checks") or [] if c.get("label") == label]
    baseline = None
    if baseline_metrics:
        row = next((r for r in (baseline_metrics.get("labels") or {}).get(kind, []) if r["label"] == label), None)
        if row:
            baseline = {"run_id": (baseline_info or {}).get("run_id"), "tag": (baseline_info or {}).get("tag"),
                        "samples": row["samples"], "p50_ms": row.get("p50_ms"), "p95_ms": row["p95_ms"],
                        "p99_ms": row.get("p99_ms"), "error_pct": row["error_pct"], "tps": row.get("tps"),
                        "p95_change_pct": round((steady["p95_ms"] / row["p95_ms"] - 1) * 100, 1) if row["p95_ms"] else None,
                        "err_change_pp": round(steady["error_pct"] - row["error_pct"], 3)}

    d = {
        "run_id": run_id, "label": label, "kind": kind,
        "steady": steady, "whole_run": whole, "ladder": ladder, "histogram": histogram,
        "share_pct": round(float(extra["total_ms"] or 0) / kind_total * 100, 2) if kind_total else 0.0,
        "stddev_ms": round(float(extra["sd"] or 0), 1),
        "timeline": {"bucket_s": bucket, "points": timeline},
        "codes": codes, "errors": errors, "slowest": slowest, "breakdown": breakdown,
        "findings": findings, "sla": sla, "baseline": baseline,
        "window": {"start_offset_s": round((ws - start_ms) / 1000), "end_offset_s": round((we - start_ms) / 1000)},
    }
    d["status"], d["synthesis"] = synthesize(d)
    return d


def _parse_start(doc: dict[str, Any]) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(doc["run"]["start"]).timestamp() * 1000)


def _names(f: dict[str, Any], label: str) -> bool:
    if label in (f.get("labels") or []):
        return True
    for v in (f.get("evidence") or {}).values():
        if isinstance(v, list) and any(isinstance(x, dict) and x.get("label") == label for x in v):
            return True
    return False


def _histogram(db: ResultsDB, p: dict, in_window: str, ladder: list[dict]) -> dict[str, Any]:
    """Response-time distribution up to the 99.9th percentile in ~24 equal bins (the rest in one overflow bin)."""
    if not ladder:
        return {"bin_ms": 0, "bins": [], "overflow": 0}
    top = max(next(x["ms"] for x in ladder if x["p"] == 0.999), 1)
    raw = top / 24
    mag = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    step = max(int(step), 1)
    rows = db.rows(f"""SELECT least(intDiv(elapsed, {step}), {int(top // step) + 1}) AS b, count()
                       FROM {db.db}.samples WHERE {in_window} GROUP BY b ORDER BY b""", p)
    last = int(top // step) + 1
    counts = {int(b): int(n) for b, n in rows}
    bins = [{"from_ms": b * step, "to_ms": (b + 1) * step, "samples": counts.get(b, 0)} for b in range(last)]
    return {"bin_ms": step, "bins": bins, "overflow": counts.get(last, 0)}


# ---------------------------------------------------------------------------
# Synthesis: what the numbers say about this label, in plain sentences
# ---------------------------------------------------------------------------

def synthesize(d: dict[str, Any]) -> tuple[str, list[str]]:
    """(status, sentences). Status: critical | attention | healthy | idle."""
    s, kind, label = d["steady"], d["kind"], d["label"]
    out: list[str] = []
    if not s["samples"]:
        return "idle", [f"{label} did not run during the steady state; see the whole-run figures."]
    rank = {"healthy": 0, "attention": 1, "critical": 2}
    status = "healthy"

    def worse(level: str) -> None:
        nonlocal status
        if rank[level] > rank[status]:
            status = level

    out.append(f"Ran {num(s['samples'])} times in the steady state ({num(s['tps'], 2)} per second), "
               f"{pct_text(d['share_pct'])} of all {kind} time.")
    out.append(f"Half the responses took up to {ms(s['p50_ms'])}, 95% up to {ms(s['p95_ms'])}, "
               f"99% up to {ms(s['p99_ms'])}; the slowest took {ms(s['max_ms'])}.")
    if s["p50_ms"] and s["p99_ms"] >= 3 * s["p50_ms"] and s["p99_ms"] - s["p50_ms"] >= 200:
        out.append(f"Long tail: the slowest 1% are {s['p99_ms'] / s['p50_ms']:.1f}× slower than a typical response, "
                   "so some users wait much longer than most.")
        worse("attention")

    if s["errors"]:
        top = d["errors"][0] if d["errors"] else None
        why = f", mostly {(top['code'] + ' ' + top['message'][:80]).strip()}" if top else ""
        out.append(f"{pct_text(s['error_pct'])} of its samples failed ({num(s['errors'])}){why}.")
        worse("critical" if s["error_pct"] >= 5 else "attention" if s["error_pct"] >= 1 else "healthy")
    else:
        out.append("No samples failed in the steady state.")

    b = d.get("breakdown")
    if b:
        parts = {"waiting for the server": b["server_ms"], "downloading the response": b["download_ms"],
                 "opening connections": b["connect_ms"]}
        main = max(parts, key=parts.get)
        out.append(f"On average {ms(b['avg_ms'])}: {ms(b['server_ms'])} waiting for the server, "
                   f"{ms(b['download_ms'])} downloading, {ms(b['connect_ms'])} connecting — most of it {main}.")

    pts = [x for x in d["timeline"]["points"]
           if d["window"]["start_offset_s"] <= x["t_offset_s"] < d["window"]["end_offset_s"] and x["samples"] >= 5]
    if len(pts) >= 6:
        third = len(pts) // 3
        first = sorted(x["p95_ms"] for x in pts[:third])[third // 2]
        last = sorted(x["p95_ms"] for x in pts[-third:])[third // 2]
        if first and last >= 1.25 * first and last - first >= 50:
            out.append(f"It slowed down during the steady state: p95 about {ms(first)} early on, {ms(last)} near the end.")
            worse("attention")
        elif first and last <= 0.8 * first and first - last >= 50:
            out.append(f"It got faster during the steady state (p95 about {ms(first)} early on, {ms(last)} near the end), "
                       "typical of warm-up.")

    for c in d["sla"]:
        verdict = "met" if c["passed"] else "missed"
        out.append(f"SLA {c['metric']} ≤ {ms(c['target'])}: {verdict} ({ms(c['actual'])}).")
        if not c["passed"]:
            worse("critical")
    if d["findings"]:
        refs = ", ".join(f"#{f['n']}" for f in d["findings"][:4])
        out.append(f"Named in finding{'s' if len(d['findings']) > 1 else ''} {refs}: {d['findings'][0]['title']}.")
        worse("critical" if any(f["severity"] == "critical" for f in d["findings"]) else "attention")
    base = d.get("baseline")
    if base and base.get("p95_change_pct") is not None:
        who = base.get("tag") or base.get("run_id")
        change = base["p95_change_pct"]
        trend = "slower" if change > 0 else "faster"
        out.append(f"Compared with {who}: p95 {ms(base['p95_ms'])} → {ms(s['p95_ms'])} "
                   f"({abs(change):.0f}% {trend}); errors {pct_text(base['error_pct'])} → {pct_text(s['error_pct'])}.")
    return status, out


# ---------------------------------------------------------------------------
# Choosing labels for the report
# ---------------------------------------------------------------------------

def report_labels(doc: dict[str, Any], kinds: dict[str, str], metrics: dict[str, Any], limit: int = 40) -> list[str]:
    """Every transaction, then requests named in findings or SLA checks, then the busiest requests."""
    labels = metrics.get("labels") or {}
    tx = [r["label"] for r in labels.get("transaction", [])]
    named = {lb for f in doc.get("findings", []) for lb in (f.get("labels") or [])}
    named |= {c["label"] for c in (doc.get("sla") or {}).get("checks") or [] if c.get("label")}
    reqs = sorted(labels.get("request", []), key=lambda r: -r["samples"])
    chosen = tx + [r["label"] for r in reqs if r["label"] in named]
    chosen += [r["label"] for r in reqs if r["label"] not in chosen]
    return [lb for lb in dict.fromkeys(chosen) if lb in kinds][:max(limit, len(tx))]


def load_baseline(doc: dict[str, Any], runs_dir: Path, db: ResultsDB) -> tuple[dict | None, dict | None]:
    """The baseline run's metrics (for per-label comparison), when the run was compared with one."""
    base = doc.get("baseline") or {}
    if not base.get("found"):
        return None, None
    from projectstack.results.compare import CompareError, load_run
    try:
        run = load_run(base["run_id"], runs_dir, db)
    except (CompareError, LookupError):
        return None, None
    return run.metrics, {"run_id": base["run_id"], "tag": base.get("tag")}


def build_report(db: ResultsDB, folder: Path, labels: list[str] | None = None, limit: int = 40,
                 out_format: str = "html") -> tuple[str, list[dict[str, Any]]]:
    """The per-label synthesis report (all transactions and the main requests), rendered."""
    from projectstack.results.report import _env
    from projectstack.results.chart import sparkline_svg
    doc = json.loads((folder / "findings.json").read_text())
    metrics = json.loads((folder / "metrics.json").read_text())
    run_id = doc["run"]["run_id"]
    kinds = label_kinds(db, run_id)
    chosen = labels or report_labels(doc, kinds, metrics, limit)
    base_metrics, base_info = load_baseline(doc, folder.parent, db)
    totals = kind_totals(db, run_id, doc)
    details = [detail(db, run_id, lb, doc, base_metrics, base_info, totals, kinds) for lb in chosen]
    from markupsafe import Markup
    if out_format == "html":
        for d in details:
            pts = d["timeline"]["points"]
            d["spark"] = {k: Markup(sparkline_svg([x[k] for x in pts], unit)) for k, unit in
                          (("p95_ms", "ms"), ("tps", "/s"), ("error_pct", "%"))}
    from datetime import datetime, timezone
    text = _env().get_template(f"report_labels.{out_format}.j2").render(
        doc=doc, run=doc["run"], details=details, total_labels=len(kinds),
        counts={k: sum(1 for v in kinds.values() if v == k) for k in ("transaction", "request")},
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), offset=offset)
    return text, details
