"""
Performance highlights and observations: the report section that says when and
how the test ran, gives its headline statistics and states what stood out.

Rules write it from findings.json plus the run's test context (region, test type
and the tester's notes, kept in context.json next to findings.json), so it is
always there and every figure is exact. An AI provider can rewrite the opening
lines and the observations in report style, using the notes to say what an
endpoint does or which limits apply; every number it writes is checked against
findings.json, the computed facts and the notes. The statistics list is always
computed, never written by a model.

Totals (requests, errors, throughput) are the Total row of JMeter's HTML report
for the file (jmeter_stats): the whole run, requests with their sub-requests,
throughput over their own first-to-last time. Response times cover the steady
state, like the rest of the analysis.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from projectstack.results import numbers
from projectstack.results.fmt import num, pct_text
from projectstack.results.narrative import Narrative, NumberCheckFailed, _cap, _check_text, _plain, _strs, parse_json

AUDIENCE = "highlights"
PROMPT = "report_highlights"
CONTEXT_FILE = "context.json"
CACHE_FILE = "narrative_highlights.json"
CONTEXT_FIELDS = {"region": 80, "test_type": 60, "notes": 4000}     # field: max length
MAX_INTRO = 5
MAX_OBSERVATIONS = 8

SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["intro", "observations"],
    "properties": {"intro": {"type": "array", "items": {"type": "string"}},
                   "observations": {"type": "array", "items": {"type": "string"}}},
}

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_OTHER_FINDINGS = ("error_burst", "error_rate_change", "server", "correlation", "injector_connect",
                   "injector_throughput")


# ---------------------------------------------------------------------------
# Test context (context.json)
# ---------------------------------------------------------------------------

def clean_context(raw: dict[str, Any] | None) -> dict[str, str]:
    """Region, test type and notes as trimmed strings (empty when not given)."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, str] = {}
    for key, limit in CONTEXT_FIELDS.items():
        value = raw.get(key)
        text = "" if value is None else str(value).strip()
        if key != "notes":
            text = " ".join(text.split())
        out[key] = text[:limit]
    return out


def has_context(ctx: dict[str, Any] | None) -> bool:
    return bool(ctx) and any(ctx.get(k) for k in CONTEXT_FIELDS)


def read_context(folder: Path) -> dict[str, str] | None:
    """The run's own context.json, or None when it has none."""
    try:
        saved = json.loads((folder / CONTEXT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    return {**clean_context(saved), "updated_at": str(saved.get("updated_at", ""))}


def save_context(folder: Path, raw: dict[str, Any]) -> dict[str, str]:
    """Write the run's context.json. An empty context is kept too: the run then stops inheriting one."""
    ctx = {**clean_context(raw), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (folder / CONTEXT_FILE).write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")
    return ctx


def resolve_context(folder: Path, scenario: str, runs_dir: Path | None = None) -> dict[str, Any]:
    """
    The context that applies to a run: its own context.json, else the latest other run of the same
    scenario's (source "inherited"). When neither exists, `suggestion` is the latest context saved for
    any other run, for the web app to offer.
    """
    own = read_context(folder)
    if own is not None:
        return {**own, "source": "run", "from_run": None, "suggestion": None}
    suggestion = None
    for run_id, other_scenario, ctx in _saved_contexts(runs_dir, exclude=folder.name):
        if scenario and other_scenario == scenario:
            return {**ctx, "source": "inherited", "from_run": run_id, "suggestion": None}
        if suggestion is None:
            suggestion = {**ctx, "from_run": run_id, "scenario": other_scenario}
    return {**clean_context(None), "updated_at": "", "source": "none", "from_run": None, "suggestion": suggestion}


def _saved_contexts(runs_dir: Path | None, exclude: str):
    """(run id, scenario, context) of every other run with a non-empty context, newest first."""
    if not runs_dir or not Path(runs_dir).is_dir():
        return
    for d in sorted(Path(runs_dir).iterdir(), key=lambda p: p.name, reverse=True):
        if d.name == exclude or not (d / CONTEXT_FILE).is_file():
            continue
        ctx = read_context(d)
        if not has_context(ctx):
            continue
        try:
            scenario = str(json.loads((d / "meta.json").read_text(encoding="utf-8")).get("scenario", ""))
        except (OSError, ValueError, AttributeError):
            scenario = ""
        yield d.name, scenario, ctx


# ---------------------------------------------------------------------------
# Facts: everything the section states, computed from findings.json
# ---------------------------------------------------------------------------

def _dt(iso: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def window_text(start_iso: str, end_iso: str) -> str:
    """November 05, 2026, 05:30 - 06:50 AM UTC (both ends written out when they differ in day or AM/PM)."""
    s, e = _dt(start_iso), _dt(end_iso)
    if not s or not e:
        return f"{start_iso} to {end_iso}"

    def day(dt: datetime) -> str:
        return f"{_MONTHS[dt.month - 1]} {dt.day:02d}, {dt.year}"

    def hm(dt: datetime) -> str:
        return f"{(dt.hour % 12) or 12:02d}:{dt.minute:02d}"

    def half(dt: datetime) -> str:
        return "AM" if dt.hour < 12 else "PM"

    if s.date() != e.date():
        return f"{day(s)}, {hm(s)} {half(s)} - {day(e)}, {hm(e)} {half(e)} UTC"
    if half(s) == half(e):
        return f"{day(s)}, {hm(s)} - {hm(e)} {half(e)} UTC"
    return f"{day(s)}, {hm(s)} {half(s)} - {hm(e)} {half(e)} UTC"


def load_profile(doc: dict[str, Any]) -> dict[str, Any]:
    """Ramp-up, time at full load and ramp-down, in seconds, and whether the ramp-up went in steps."""
    run, st = doc["run"], doc.get("steady") or {}
    duration = float(run.get("duration_s") or 0)
    peak = run.get("peak_users") or st.get("peak_users") or 0
    if not st or "fallback" in str(st.get("method", "")) or st.get("start_offset_s") is None:
        return {"kind": "unsteady", "duration_s": round(duration), "peak_users": peak}
    start = _dt(run.get("start", ""))
    start_ms = start.timestamp() * 1000 if start else None
    if st.get("plateau_start_ms") and st.get("plateau_end_ms") and start_ms is not None:
        a, b = (st["plateau_start_ms"] - start_ms) / 1000, (st["plateau_end_ms"] - start_ms) / 1000
    elif st.get("source") == "auto":
        # Older findings.json: undo the edge trim (10% of the plateau, at most 60 s each side)
        d = float(st.get("duration_s") or 0)
        trim = 60.0 if d >= 480 else d / 8
        a, b = st["start_offset_s"] - trim, st["end_offset_s"] + trim
    else:
        a, b = float(st["start_offset_s"]), float(st["end_offset_s"])     # a window given with --steady
    a = max(0.0, a)
    b = max(a, min(b, duration) if duration else b)
    lower = [lv for lv in st.get("levels") or []
             if start_ms is not None and lv.get("users", 0) < 0.95 * peak and (lv["start_ms"] - start_ms) / 1000 < a]
    ramp = round(a)
    kind = "constant" if ramp < 60 else "stepped" if len(lower) >= 2 else "ramp"
    out = {"kind": kind, "ramp_up_s": ramp, "hold_s": round(b - a), "ramp_down_s": round(max(0.0, duration - b)),
           "duration_s": round(duration), "peak_users": peak}
    if kind == "stepped":
        out["steps"] = len(lower) + 1
    return out


_TYPE_WORDS = ((("smoke",), "smoke test"), (("soak", "endurance"), "endurance test"), (("stress",), "stress test"),
               (("spike",), "spike test"))


def test_type(doc: dict[str, Any], profile: dict[str, Any], given: str = "") -> str:
    if given:
        return given if given.lower().endswith("test") else f"{given} test"
    name = f"{doc['run'].get('scenario', '')} {doc['run'].get('tag', '')}".lower()
    for words, kind in _TYPE_WORDS:
        if any(w in name for w in words):
            return kind
    if profile["kind"] == "stepped":
        return "incremental load test"
    if profile.get("hold_s", 0) >= 4 * 3600:
        return "endurance test"
    return "load test"


def totals(doc: dict[str, Any]) -> dict[str, Any]:
    """Requests, errors and average throughput over the whole run: the Total row of JMeter's HTML report.

    findings.json written before that row was kept in it falls back to the timeline's sums (requests
    without their sub-requests, over the run's duration).
    """
    jt = doc.get("jmeter_total") or {}
    if jt.get("samples"):
        return {"requests": int(jt["samples"]), "errors": int(jt["errors"]),
                "error_pct": round(float(jt["error_pct"]), 3), "throughput_per_s": round(float(jt["tps"]), 2),
                "duration_s": round(float(doc["run"].get("duration_s") or 0), 1), "scope": "whole run"}
    points = (doc.get("timeline") or {}).get("points") or []
    requests = sum(int(p.get("samples") or 0) for p in points)
    errors = sum(int(p.get("errors") or 0) for p in points)
    duration, scope = float(doc["run"].get("duration_s") or 0), "whole run"
    if not requests:
        overall = (doc.get("overall") or {}).get("request") or {}
        requests, errors = int(overall.get("samples") or 0), int(overall.get("errors") or 0)
        duration, scope = float((doc.get("steady") or {}).get("duration_s") or 0), "steady state"
    return {"requests": requests, "errors": errors,
            "error_pct": round(errors / requests * 100, 3) if requests else 0.0,
            "throughput_per_s": round(requests / duration, 2) if duration else 0.0,
            "duration_s": round(duration, 1), "scope": scope}


_URL_IN_BRACKETS = re.compile(r"\[([^\]]*/[^\]]*)\]")
_METHOD = re.compile(r"^(?:\[\d+\]\s*)?(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+", re.I)


def endpoint(label: str, transactions: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    """
    How to name a label in prose: its URL path when the label carries one ("US - SC05 [host/api/x] - 001",
    "GET /api/x"), else the label itself; plus the transaction it belongs to when a transaction label
    shares its prefix ("US - SC05 - Checkout" for "US - SC05 [...]").
    """
    m = _URL_IN_BRACKETS.search(label)
    target = (m.group(1) if m else _METHOD.sub("", label)).strip()
    target = re.sub(r"^[a-z][a-z0-9+.-]*://", "", target, flags=re.I)
    path = ""
    head = target.split("/", 1)[0]
    if target.startswith("/"):
        path = target
    elif "/" in target and "." in head and " " not in head:
        path = "/" + target.split("/", 1)[1]
    path = re.split(r"[?#\s]", path, maxsplit=1)[0] if path else ""
    shown = path if path and path != "/" else (target if m else label)
    name = ""
    if m:
        prefix = label[:m.start()].strip().rstrip("-").strip()
        hits = [t for t in transactions if prefix and t.startswith(prefix + " - ")]
        if len(hits) == 1:
            name = hits[0][len(prefix) + 3:].strip()
    return {"label": label, "path": path, "shown": shown, "name": name}


def _named(ep: dict[str, Any]) -> str:
    return f"{ep['shown']} ({ep['name']})" if ep.get("name") else ep["shown"]


def endpoints(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every request label (transactions when the plan has no requests) with its name and statistics."""
    labels = doc.get("labels") or {}
    rows = labels.get("request") or labels.get("transaction") or []
    txns = [t["label"] for t in labels.get("transaction") or []]
    keys = ("samples", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "error_pct", "run_samples", "run_errors")
    return [{**endpoint(r["label"], txns), **{k: r.get(k) for k in keys if r.get(k) is not None}} for r in rows]


def slowest(eps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The endpoint with the highest median among those with a fair share of the traffic."""
    total = sum(e.get("samples") or 0 for e in eps)
    floor = max(20, 0.005 * total)
    big = [e for e in eps if (e.get("samples") or 0) >= floor and e.get("p50_ms") is not None]
    return max(big, key=lambda e: (e["p50_ms"], e.get("p95_ms") or 0), default=None)


def facts(doc: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """The computed facts behind the section (also what the AI provider gets besides findings.json)."""
    ctx = clean_context(ctx)
    run = doc["run"]
    profile = load_profile(doc)
    overall = (doc.get("overall") or {}).get("request") or (doc.get("overall") or {}).get("transaction") or {}
    eps = endpoints(doc)
    slow = slowest(eps)
    if slow and overall.get("p50_ms"):
        slow = {**slow, "times_overall_median": round(slow["p50_ms"] / max(overall["p50_ms"], 1), 1)}
    top = sorted(eps, key=lambda e: -(e.get("p95_ms") or 0))[:8]
    base = doc.get("baseline") or {}
    return {
        "window": {"start": run.get("start"), "end": run.get("end"), "text": window_text(run.get("start", ""),
                                                                                         run.get("end", ""))},
        "test_type": test_type(doc, profile, ctx["test_type"]),
        "test_type_source": "context" if ctx["test_type"] else "inferred",
        "region": ctx["region"],
        "peak_users": run.get("peak_users"),
        "load_profile": profile,
        "totals": totals(doc),
        "response_times": {**{k: overall.get(k) for k in ("samples", "avg_ms", "p50_ms", "p90_ms", "p95_ms",
                                                            "p99_ms", "max_ms") if overall.get(k) is not None},
                           "scope": "steady state, all requests"},
        "slowest_endpoint": slow,
        "endpoints_by_p95": top,
        "baseline": {"run_id": base.get("run_id"), "comparable": (base.get("load") or {}).get("comparable"),
                     "improvements": base.get("improvements") or []} if base.get("found") else None,
    }


# ---------------------------------------------------------------------------
# Rule-based section
# ---------------------------------------------------------------------------

def _span(seconds: float) -> tuple[str, str | None]:
    """("58 minutes", "58-minute"); the adjective is None for mixed units ("1 hour 15 minutes")."""
    minutes = round((seconds or 0) / 60)
    if minutes < 1:
        s = round(seconds or 0)
        return f"{s} second{'s' * (s != 1)}", f"{s}-second"
    if minutes < 60:
        return f"{minutes} minute{'s' * (minutes != 1)}", f"{minutes}-minute"
    h, m = divmod(minutes, 60)
    hours = f"{h} hour{'s' * (h != 1)}"
    return (hours, f"{h}-hour") if not m else (f"{hours} {m} minute{'s' * (m != 1)}", None)


def _article(word: str) -> str:
    digits = re.match(r"\d+", word or "")
    d = digits.group(0) if digits else ""
    return "an" if d.startswith("8") or (d[:2] in ("11", "18") and len(d) in (2, 5)) else "a"


def _profile_sentence(p: dict[str, Any]) -> str:
    if p["kind"] == "unsteady":
        return (f"The load never held steady at its peak during the {_span(p['duration_s'])[0]} run, so response "
                "times cover the whole run.")
    hold = _span(p["hold_s"])[0]
    if p["kind"] == "constant":
        text = f"The test ran at full load from the start, with {hold} of steady-state execution"
    else:
        noun, adj = _span(p["ramp_up_s"])
        what = "stepped ramp-up" if p["kind"] == "stepped" else "ramp-up"
        ramp = f"{_article(adj)} {adj} {what}" if adj else f"a {what} of {noun}"
        text = f"The test included {ramp} followed by {hold} of steady-state execution"
    down = p.get("ramp_down_s", 0)
    if down >= 120 and down >= 0.05 * p["duration_s"]:
        noun, adj = _span(down)
        text += f", then {_article(adj)} {adj} ramp-down" if adj else f", then a ramp-down of {noun}"
    return text + "."


def intro(f: dict[str, Any]) -> list[str]:
    region = f["region"]
    where = (f" from the {region}" if region.lower().endswith("region") else f" from the {region} region") if region else ""
    return [f"The test was executed on {f['window']['text']}.",
            f"The {f['test_type']} was executed with {num(f['peak_users'])} concurrent users{where}.",
            _profile_sentence(f["load_profile"])]


def stats(f: dict[str, Any]) -> list[dict[str, str]]:
    t = f["totals"]
    return [{"name": "Maximum users", "value": num(f["peak_users"])},
            {"name": "Total requests", "value": num(t["requests"])},
            {"name": "Average throughput", "value": f"{t['throughput_per_s']:,.2f} hit/sec"},
            {"name": "Total errors", "value": num(t["errors"]) + (f" ({pct_text(t['error_pct'])})" if t["errors"] else "")}]


def _volume(doc: dict[str, Any], f: dict[str, Any]) -> str:
    t = f["totals"]
    rate = f"{t['throughput_per_s']:,.2f}"
    during = " during the steady state" if t["scope"] == "steady state" else ""
    if not t["errors"]:
        return (f"Executed {num(t['requests'])} total requests{during} with zero error occurrences, attaining a "
                f"combined throughput of {rate} req/sec.")
    text = (f"Executed {num(t['requests'])} total requests{during} with {num(t['errors'])} errors "
            f"({pct_text(t['error_pct'])} of requests), at a combined throughput of {rate} req/sec.")
    top = (doc.get("errors") or [None])[0]
    if top:
        txns = [x["label"] for x in (doc.get("labels") or {}).get("transaction") or []]
        what = " ".join(x for x in (str(top.get("response_code") or ""),
                                    str(top.get("failure") or top.get("response_message") or "")) if x).strip()
        text += (f" The most frequent was {what or 'an error'} on {_named(endpoint(top['label'], txns))} "
                 f"({num(top['count'])} occurrences).")
    return text


def _latency(doc: dict[str, Any], f: dict[str, Any]) -> str:
    rt = f["response_times"]
    if rt.get("p95_ms") is None or rt.get("avg_ms") is None:
        return ""
    avg, p50, p95, p99 = rt["avg_ms"], rt.get("p50_ms") or 0, rt["p95_ms"], rt.get("p99_ms") or rt["p95_ms"]
    degraded = [x for x in doc.get("findings", [])
                if x["type"] in ("latency_change", "saturation") and x["severity"] in ("critical", "warning")]
    if degraded:
        title = degraded[0]["title"]
        return (f"Response times did not hold steady: {title[:1].lower()}{title[1:]}. Over the steady state, the "
                f"mean response time was {num(avg)} ms ({num(p95)} ms p95, {num(p99)} ms p99).")
    if p99 <= 2 * p95 and p95 <= 4 * max(p50, 1):
        return f"Sustained highly consistent response times: {num(avg)} ms mean ({num(p95)} ms p95, along with {num(p99)} ms p99)."
    if p99 <= 3 * p95:
        return f"Sustained consistent response times: {num(avg)} ms mean ({num(p95)} ms p95, along with {num(p99)} ms p99)."
    text = (f"Response times were consistent for most requests, at {num(avg)} ms mean and {num(p95)} ms p95, but the "
            f"slowest 1% took longer than {num(p99)} ms (p99).")
    mx = rt.get("max_ms")
    if mx and mx >= 10_000 and mx >= 5 * p99:
        worst = next((e for e in endpoints(doc) if e.get("max_ms") == mx), None)
        where = f", on {_named(worst)}" if worst else ""
        text += f" The single slowest request took {mx / 1000:,.0f} seconds{where}."
    return text


def _sla(doc: dict[str, Any]) -> list[str]:
    sla = doc.get("sla") or {}
    if not sla.get("defined"):
        return []
    checks = sla.get("checks") or []
    failed = [c for c in checks if not c["passed"]]
    if not failed:
        targets = "; ".join(sla.get("targets") or [])
        return [f"Met all {len(checks)} SLA targets over the steady state{f' ({targets})' if targets else ''}."]
    return [f"{_cap(_check_text(c, plain=True))}." for c in failed[:2]]


def _endpoint_points(doc: dict[str, Any], f: dict[str, Any]) -> list[str]:
    txns = [x["label"] for x in (doc.get("labels") or {}).get("transaction") or []]
    out: list[str] = []
    used: set[str] = set()
    base = f.get("baseline")
    if base and base.get("comparable") is not False:
        for imp in base["improvements"][:2]:
            ep = _named(endpoint(imp["label"], txns))
            if "p95" in imp.get("metrics", ["p95"]):
                median = (f"averaging {num(imp['p50_after'])} ms median response times ({num(imp['p95_after'])} ms "
                          f"p95, down from {num(imp['p95_before'])} ms)") if imp.get("p50_after") is not None else \
                    f"with {num(imp['p95_after'])} ms p95, down from {num(imp['p95_before'])} ms"
                out.append(f"{_cap(ep)} demonstrated marked gains over the baseline run, {median} over "
                           f"{num(imp['samples'])} invocations at full load.")
            else:
                out.append(f"{_cap(ep)} became more reliable than in the baseline run: errors fell from "
                           f"{pct_text(imp['err_before'])} to {pct_text(imp['err_after'])} over {num(imp['samples'])} "
                           "invocations at full load.")
            used.add(imp["label"])
    for x in doc.get("findings", []):
        if x["type"] != "regression" or x["severity"] == "info":
            continue
        ev = x.get("evidence") or {}
        rows = ev.get("labels") or []
        if not rows:
            continue
        w = rows[0]
        ep = _named(endpoint(w["label"], txns))
        more = (ev.get("count") or len(rows)) - 1
        tail = f" ({more} more {'label' if more == 1 else 'labels'} also regressed)" if more > 0 else ""
        if ev.get("metric") == "p95_ms":
            out.append(f"{_cap(ep)} slowed down compared with the baseline run: p95 went from {num(w.get('p95_before'))} "
                       f"ms to {num(w.get('p95_after'))} ms{tail}.")
        else:
            out.append(f"{_cap(ep)} failed more often than in the baseline run: errors went from "
                       f"{pct_text(w.get('err_before'))} to {pct_text(w.get('err_after'))}{tail}.")
        used.update(r["label"] for r in rows)
        break
    slow, rt = f.get("slowest_endpoint"), f["response_times"]
    eps = endpoints(doc)
    if slow and slow["label"] not in used and rt.get("p95_ms") and (slow.get("p95_ms") or 0) >= 2 * rt["p95_ms"]:
        ratio = slow.get("times_overall_median") or 0
        tail = f", about {ratio:.0f}× the overall median" if ratio >= 2 else ""
        errs = f", with {pct_text(slow['error_pct'])} errors" if slow.get("error_pct") else ""
        out.append(f"The slowest endpoint was {_named(slow)}, at {num(slow['p50_ms'])} ms median "
                   f"({num(slow['p95_ms'])} ms p95) over {num(slow['samples'])} invocations at full load{tail}{errs}.")
    elif eps and not out and len(eps) > 1:
        worst = max((e.get("p95_ms") or 0) for e in eps)
        out.append(f"No endpoint stood out: all {len(eps)} endpoints kept their p95 at or below {num(worst)} ms.")
    return out


def observations(doc: dict[str, Any], f: dict[str, Any]) -> list[str]:
    out = [_volume(doc, f)]
    out += [x for x in (_latency(doc, f),) if x]
    out += _sla(doc)
    out += _endpoint_points(doc, f)
    # Two findings can read the same in plain words (a login redirect and a later 401 both mean the
    # session was not carried along): say it once
    others = dict.fromkeys(_plain(x) for x in doc.get("findings", [])
                           if x["type"] in _OTHER_FINDINGS and x["severity"] in ("critical", "warning"))
    out += [x for x in others if x not in out][:2]
    return out[:MAX_OBSERVATIONS]


def rules(doc: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """The section written by rules: {intro, stats, observations}."""
    f = facts(doc, ctx)
    return {"intro": intro(f), "stats": stats(f), "observations": observations(doc, f)}


def from_rules(doc: dict[str, Any], ctx: dict[str, Any] | None = None, note: str = "") -> Narrative:
    return Narrative(AUDIENCE, rules(doc, ctx), "rules", note=note)


# ---------------------------------------------------------------------------
# AI-written section
# ---------------------------------------------------------------------------

def allowed_numbers(doc: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> numbers.Allowed:
    """Numbers the AI text may cite: findings.json, the computed facts and the tester's notes."""
    allowed = numbers.Allowed(doc).add(f).add({"notes": ctx.get("notes", "")})
    names = [ctx.get("region"), ctx.get("test_type")]
    for e in (f.get("endpoints_by_p95") or []) + [f.get("slowest_endpoint") or {}]:
        names += [e.get("shown"), e.get("path"), e.get("name")]
    for e in endpoints(doc):
        names += [e["shown"], e["path"], e["name"]]
    allowed.protected.update(n for n in names if n and len(n) >= 2)
    return allowed


def from_llm(doc: dict[str, Any], ctx: dict[str, Any] | None, provider, *, strict: bool = False) -> Narrative:
    from projectstack.llm.prompts import load_prompt
    ctx = clean_context(ctx)
    prompt = load_prompt(PROMPT)
    f = facts(doc, ctx)
    base = rules(doc, ctx)
    system, user = prompt.render(
        run_id=doc["run"]["run_id"], scenario=doc["run"].get("scenario", ""),
        region=ctx["region"] or "(not given)", test_type=ctx["test_type"] or "(not given)",
        notes=ctx["notes"] or "(none)",
        facts_json=json.dumps(f, ensure_ascii=False),
        draft_json=json.dumps({"intro": base["intro"], "observations": base["observations"]}, ensure_ascii=False),
        findings_json=json.dumps(doc, separators=(",", ":"), ensure_ascii=False),
    )
    allowed = allowed_numbers(doc, f, ctx)
    usage = {"input_tokens": 0, "output_tokens": 0}

    def ask(text: str) -> tuple[dict[str, Any], numbers.NumberCheck, str]:
        comp = provider.complete(system, text, max_tokens=8000, json_schema=SCHEMA, effort="medium")
        usage["input_tokens"] += comp.input_tokens or 0
        usage["output_tokens"] += comp.output_tokens or 0
        data = parse_json(comp.text)
        content = {"intro": _strs(data.get("intro"), MAX_INTRO) or base["intro"], "stats": base["stats"],
                   "observations": _strs(data.get("observations"), MAX_OBSERVATIONS) or base["observations"]}
        written = {"intro": content["intro"], "observations": content["observations"]}
        return content, numbers.check(written, doc, allowed), comp.model

    content, check, model = ask(user)
    attempts = 1
    if strict and not check.ok:
        listed = "\n".join(f"- “{u['text']}” in {u['field']}" for u in check.unverified)
        content, check, model = ask(
            f"{user}\n\nYour previous answer was:\n<previous_answer>\n"
            f"{json.dumps({'intro': content['intro'], 'observations': content['observations']}, ensure_ascii=False)}\n"
            f"</previous_answer>\n\nThese numbers in it do not appear in the facts, findings.json or the notes:\n"
            f"{listed}\n\nReturn the full JSON again with those statements rewritten: use figures from the facts, "
            "findings.json or the notes, or make the point in words.")
        attempts = 2
    label = f"{provider.name}/{model}" if model else provider.label
    narrative = Narrative(AUDIENCE, content, "llm", label, prompt.ref, check, usage, attempts=attempts)
    if strict and not check.ok:
        raise NumberCheckFailed(narrative)
    return narrative


def sha(doc: dict[str, Any], ctx: dict[str, Any] | None) -> str:
    """What an AI-written section depends on: findings.json and the test context."""
    payload = {"findings": doc, "context": clean_context(ctx)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cached(folder: Path, doc: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any] | None:
    """The saved AI-written section, while findings.json, the context and the prompt are unchanged."""
    from projectstack.llm.prompts import load_prompt
    try:
        saved = json.loads((folder / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (saved.get("source") != "llm" or saved.get("findings_sha256") != sha(doc, ctx)
            or saved.get("prompt") != load_prompt(PROMPT).ref):
        return None
    return {"content": saved["content"], "provider": saved.get("provider"), "prompt": saved.get("prompt"),
            "numbers": saved.get("numbers"), "generated_at": saved.get("generated_at")}


def get(doc: dict[str, Any], folder: Path, ctx: dict[str, Any] | None, *, use_llm: bool,
        provider_spec: str | None = None, strict: bool = False, fresh: bool = False,
        settings: dict[str, str] | None = None,
        progress: Callable[[str], None] = lambda _: None) -> tuple[Narrative, bool, list[str]]:
    """(section, reused from cache, warnings): AI-written when asked and possible, else by rules."""
    from projectstack.llm.base import LLMConfigError, LLMError
    from projectstack.llm.prompts import load_prompt
    from projectstack.llm.registry import provider_for_task
    from projectstack.results.report import ReportError

    if not use_llm:
        return from_rules(doc, ctx), False, []
    try:
        provider = provider_for_task("report", settings or {}, provider_spec)
    except LLMConfigError as exc:
        if provider_spec:
            raise ReportError(str(exc)) from exc
        note = f"AI highlights unavailable: {exc}"
        return from_rules(doc, ctx, note=note), False, [f"{note} — wrote the rule-based highlights instead"]

    cache, key, prompt_ref = folder / CACHE_FILE, sha(doc, ctx), load_prompt(PROMPT).ref
    if not fresh and cache.exists():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if (saved.get("findings_sha256") == key and saved.get("prompt") == prompt_ref
                    and saved.get("provider_requested") == provider.label and saved.get("source") == "llm"
                    and (not strict or (saved.get("numbers") or {}).get("ok"))):
                return Narrative.from_dict(saved), True, []
        except (OSError, ValueError, KeyError):
            pass

    progress(f"Writing the performance highlights with {provider.label} …")
    try:
        narrative = from_llm(doc, ctx, provider, strict=strict)
    except NumberCheckFailed:
        raise
    except LLMError as exc:
        if provider_spec or strict:
            raise ReportError(str(exc)) from exc
        note = f"AI highlights failed: {exc}"
        return from_rules(doc, ctx, note=note), False, [f"{note} — wrote the rule-based highlights instead"]
    cache.write_text(json.dumps({**narrative.as_dict(), "findings_sha256": key, "provider_requested": provider.label},
                                indent=2, ensure_ascii=False), encoding="utf-8")
    warnings = []
    if narrative.numbers and not narrative.numbers.ok:
        warnings.append(f"Highlights number check: {narrative.numbers.describe('the results and notes')}")
    return narrative, False, warnings
