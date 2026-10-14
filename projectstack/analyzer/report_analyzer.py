"""
JMeter HTML dashboard analyzer.

Reads the data files JMeter writes next to its HTML report (statistics.json,
content/js/dashboard.js, content/js/graph.js, index.html) and turns them into
high-level, rule-based insights and a ranked list of findings. No LLM is
needed; an AI narrative can be layered on top.

Pass/fail is judged only against an SLA the user defined before the run
(see sla.py). Without one the verdict is "no_sla": the findings describe what
was measured, and no response-time or error-rate targets are assumed.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

# Response-time spread below this is noise, not an observation worth reporting
_SPREAD_FLOOR_MS = 200

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "good": 3}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _extract_json_after(text: str, marker: str) -> Any | None:
    """Decode the JSON value that starts right after `marker` in `text`."""
    idx = text.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
        return value
    except ValueError:
        return None


def _table(dashboard_js: str, table_id: str) -> dict | None:
    return _extract_json_after(dashboard_js, f'createTable($("#{table_id}"),')


def _series(graph_js: str, name: str) -> list[dict]:
    data = _extract_json_after(graph_js, f"var {name}Infos = {{\n        data:")
    if data is None:  # tolerate formatting differences between JMeter versions
        m = re.search(rf"var {name}Infos\s*=\s*\{{\s*data:\s*", graph_js)
        if m:
            try:
                data, _ = json.JSONDecoder().raw_decode(graph_js, m.end())
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return []
    return (data.get("result") or {}).get("series") or []


def parse_html_report(html_dir: str | Path) -> dict[str, Any]:
    """Parse a JMeter HTML dashboard directory into plain data."""
    root = Path(html_dir)
    stats_file = root / "statistics.json"
    if not stats_file.exists():
        raise FileNotFoundError(f"statistics.json not found in {root}")

    stats: dict[str, dict] = json.loads(stats_file.read_text())
    total = stats.pop("Total", None)

    dashboard_js = _read(root / "content" / "js" / "dashboard.js")
    graph_js = _read(root / "content" / "js" / "graph.js")
    index_html = _read(root / "index.html")

    apdex = _table(dashboard_js, "apdexTable") or {}
    errors = _table(dashboard_js, "errorsTable") or {}
    top5 = _table(dashboard_js, "top5ErrorsBySamplerTable") or {}

    def info(label: str) -> str | None:
        m = re.search(rf"<td>{label}</td>\s*<td>\"?([^<\"]*)\"?</td>", index_html)
        return m.group(1).strip() if m else None

    return {
        "source": "jmeter_html",
        "start_time": info("Start Time"),
        "end_time": info("End Time"),
        "total": total,
        "transactions": list(stats.values()),
        "apdex": {
            "overall": (apdex.get("overall") or {}).get("data"),
            "items": [i.get("data") for i in apdex.get("items", [])],
        },
        "errors": [i.get("data") for i in errors.get("items", [])],
        "top5_errors": [i.get("data") for i in top5.get("items", [])],
        "series": {
            "response_times": _series(graph_js, "responseTimesOverTime"),
            "active_threads": _series(graph_js, "activeThreadsOverTime"),
            "total_tps": _series(graph_js, "totalTPS"),
            "codes": _series(graph_js, "codesPerSecond"),
        },
    }


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def from_session_results(results: dict) -> dict[str, Any]:
    """Build the same shape from the JTL summary when no HTML report exists."""
    txns = []
    for label, m in (results.get("by_label") or {}).items():
        txns.append({
            "transaction": label,
            "sampleCount": m.get("total", 0),
            "errorCount": m.get("failed", 0),
            "errorPct": m.get("error_rate", 0.0),
            "meanResTime": m.get("avg_ms", 0.0),
            "medianResTime": None,
            "minResTime": None,
            "maxResTime": None,
            "pct1ResTime": None,
            "pct2ResTime": m.get("p95_ms"),
            "pct3ResTime": None,
            "throughput": None,
        })
    total = {
        "transaction": "Total",
        "sampleCount": results.get("total_requests", 0),
        "errorCount": results.get("failed", 0),
        "errorPct": results.get("error_rate", 0.0),
        "meanResTime": results.get("avg_ms", 0.0),
        "minResTime": results.get("min_ms"),
        "maxResTime": results.get("max_ms"),
        "pct1ResTime": results.get("p90_ms"),
        "pct2ResTime": results.get("p95_ms"),
        "pct3ResTime": None,
        "medianResTime": None,
        "throughput": results.get("throughput"),
    }
    return {
        "source": "jtl_summary",
        "start_time": None, "end_time": None,
        "total": total, "transactions": txns,
        "apdex": {"overall": None, "items": []},
        "errors": [], "top5_errors": [],
        "series": {"response_times": [], "active_threads": [], "total_tps": [], "codes": []},
        "duration_sec": results.get("duration_sec"),
    }


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def _num(v: Any) -> float | None:
    """A finite number or None (JMeter's dashboard writes "Infinity"/NaN for some stats of very short runs)."""
    try:
        f = None if v is None else float(v)
    except (TypeError, ValueError):
        return None
    return f if f is not None and math.isfinite(f) else None


def _ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.2f} s" if v >= 1000 else f"{round(v)} ms"


def _apdex_rating(score: float) -> str:
    if score >= 0.94:
        return "Excellent"
    if score >= 0.85:
        return "Good"
    if score >= 0.70:
        return "Fair"
    if score >= 0.50:
        return "Poor"
    return "Unacceptable"


def _sla_check_title(c: dict) -> tuple[str, str, str]:
    """(title, detail, metric) for a failed SLA check."""
    unit, target, actual = c["unit"], c["target"], c.get("actual")
    if c["kind"] == "response_time":
        shown = _ms(actual) if actual is not None else "not measured"
        detail = c.get("note") or f"{c['metric']} was {shown}; the SLA allows at most {_ms(target)}."
        return f"SLA missed: {c['label']} response time", detail, shown
    if c["kind"] == "error_rate":
        shown = f"{actual:g}%" if actual is not None else "not measured"
        return "SLA missed: error rate", f"{shown} of requests failed; the SLA allows at most {target:g}%.", shown
    shown = f"{actual:g} req/s" if actual is not None else "not measured"
    return "SLA missed: throughput", f"Throughput was {shown}; the SLA requires at least {target:g} req/s.", shown


def derive_insights(report: dict[str, Any], sla: dict | None = None) -> dict[str, Any]:
    """
    Turn parsed report data into KPIs and ranked findings. `sla` is the result
    of sla.evaluate_sla for this run; without a defined SLA the run is
    described, not judged.
    """
    sla = sla if sla and sla.get("defined") else None
    total = report.get("total") or {}
    txns: list[dict] = report.get("transactions") or []
    findings: list[dict] = []

    def add(severity: str, title: str, detail: str, metric: str | None = None) -> None:
        findings.append({"severity": severity, "title": title, "detail": detail, "metric": metric})

    samples = int(total.get("sampleCount") or 0)
    error_pct = _num(total.get("errorPct")) or 0.0
    mean = _num(total.get("meanResTime"))
    median = _num(total.get("medianResTime"))
    p90 = _num(total.get("pct1ResTime"))
    p95 = _num(total.get("pct2ResTime"))
    p99 = _num(total.get("pct3ResTime"))
    tps = _num(total.get("throughput"))

    # JMeter always computes Apdex (with its own 500 ms / 1500 ms defaults). It is
    # only meaningful here when the run had a response-time SLA, which sets T.
    apdex_overall = report.get("apdex", {}).get("overall")
    use_apdex = bool(sla and (sla.get("config") or {}).get("response_time_ms") and apdex_overall)
    apdex_score = _num(apdex_overall[0]) if use_apdex else None
    t_ms = _num(apdex_overall[1]) if use_apdex else None
    f_ms = _num(apdex_overall[2]) if use_apdex else None

    # ── SLA ──────────────────────────────────────────────────────────────
    if sla:
        failed = [c for c in sla.get("checks", []) if not c["passed"]]
        for c in failed:
            title, detail, metric = _sla_check_title(c)
            add("critical", title, detail, metric)
        if sla.get("checks") and not failed:
            add("good", "All SLA targets met",
                f"{len(sla['checks'])} check{'s' if len(sla['checks']) != 1 else ''} passed: {'; '.join(sla.get('targets', []))}.",
                f"{len(sla['checks'])}/{len(sla['checks'])}")

    # ── Reliability ──────────────────────────────────────────────────────
    if samples == 0:
        add("critical", "No samples recorded", "The report contains no samples, so the test did not exercise the system.")
    elif error_pct == 0:
        add("good", "No failed requests", f"All {samples:,} samples succeeded.", "0% errors")
    else:
        add("warning", "Failed requests",
            f"{error_pct:.2f}% of samples failed ({int(total.get('errorCount') or 0):,} of {samples:,}).",
            f"{error_pct:.2f}% errors")

    errors = [e for e in report.get("errors") or [] if e]
    if errors:
        top = max(errors, key=lambda e: e[1] or 0)
        failing = [t["transaction"] for t in txns if (_num(t.get("errorCount")) or 0) > 0]
        where = f" Affected: {', '.join(failing[:4])}{'…' if len(failing) > 4 else ''}." if failing else ""
        add("warning", f"Most common error: {top[0]}",
            f"{int(top[1]):,} occurrences, {top[2]:.0f}% of all errors.{where}", str(top[0]))

    codes = report.get("series", {}).get("codes") or []
    bad_codes = sorted({s.get("label") for s in codes if s.get("label") and not str(s.get("label")).startswith(("2", "3"))})
    if bad_codes:
        add("warning", "Non-success HTTP status codes", f"Responses included status {', '.join(bad_codes)}.", ", ".join(bad_codes))

    # ── User experience (Apdex against the SLA's response-time target) ───
    if apdex_score is not None:
        add("info", f"Apdex {apdex_score:.2f} — {_apdex_rating(apdex_score)}",
            f"Measured against the SLA: satisfied ≤ {_ms(t_ms)}, frustrated > {_ms(f_ms)}.", f"{apdex_score:.2f}")

    # ── Latency (observations — targets come only from the SLA) ──────────
    ranked = sorted((t for t in txns if _num(t.get("pct2ResTime")) is not None),
                    key=lambda t: _num(t.get("pct2ResTime")) or 0, reverse=True)
    if len(ranked) > 1:
        names = ", ".join(f"{t['transaction']} ({_ms(_num(t.get('pct2ResTime')))})" for t in ranked[:3])
        add("info", "Slowest transactions by p95", f"{names}.", _ms(_num(ranked[0].get("pct2ResTime"))))

    # Tail spread: p99 far above the median means inconsistent behaviour
    spread = []
    for t in txns:
        med, tail = _num(t.get("medianResTime")), _num(t.get("pct3ResTime"))
        if med and tail and med > 0 and tail / med >= 3 and tail - med >= _SPREAD_FLOOR_MS:
            spread.append((tail / med, t))
    spread.sort(key=lambda x: x[0], reverse=True)
    if spread:
        ratio, t = spread[0]
        add("warning", "Inconsistent response times",
            f"{t['transaction']} has p99 {_ms(_num(t.get('pct3ResTime')))} vs median {_ms(_num(t.get('medianResTime')))} "
            f"({ratio:.1f}×). Spikes like this usually point to contention, GC pauses or cold caches.", f"{ratio:.1f}× spread")

    # Where the time goes
    weights = [(((_num(t.get("meanResTime")) or 0) * (_num(t.get("sampleCount")) or 0)), t) for t in txns]
    total_weight = sum(w for w, _ in weights)
    if total_weight > 0 and len(txns) > 1:
        w, t = max(weights, key=lambda x: x[0])
        share = w / total_weight * 100
        if share >= 30:
            add("info", "Biggest contributor to response time",
                f"{t['transaction']} accounts for {share:.0f}% of total time spent waiting on responses — "
                "the best candidate to optimise first.", f"{share:.0f}%")

    # ── Trend over time ──────────────────────────────────────────────────
    overall = _overall_series(report.get("series", {}).get("response_times") or [])
    if len(overall) >= 3:
        third = max(1, len(overall) // 3)
        first = sum(overall[:third]) / third
        last = sum(overall[-third:]) / third
        if first > 0 and last / first >= 1.3:
            add("warning", "Response times degrade over the run",
                f"Average response time rose from {_ms(first)} at the start to {_ms(last)} at the end "
                f"(+{(last / first - 1) * 100:.0f}%), which can indicate saturation or a resource leak.",
                f"+{(last / first - 1) * 100:.0f}%")
        elif first > 0 and last / first <= 0.8:
            add("info", "Response times improve over the run",
                f"Average response time fell from {_ms(first)} to {_ms(last)} — likely warm-up (caches, JIT, connection pools).")

    threads = report.get("series", {}).get("active_threads") or []
    peak_threads = max((pt[1] for s in threads for pt in s.get("data", []) if len(pt) > 1), default=None)

    # ── Statistical confidence ───────────────────────────────────────────
    if 0 < samples < 100:
        add("info", "Small sample size",
            f"Only {samples} samples — percentiles are indicative, not reliable. "
            "Run longer or with more users before drawing conclusions.", f"{samples} samples")

    if not sla:
        add("info", "No SLA defined",
            "This run is not marked pass or fail. To judge it, define an SLA (response time, error rate or "
            "throughput) in the Generate Script step before running the test.")

    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f["severity"], 9))

    if sla:
        checks = sla.get("checks") or []
        verdict = "pass" if sla.get("passed") else "fail"
        score: int | None = round(sla.get("passed_count", 0) / len(checks) * 100) if checks else None
        headline = (
            f"All {len(checks)} SLA checks passed." if verdict == "pass"
            else f"{sla.get('failed_count', 0)} of {len(checks)} SLA checks failed."
        )
    else:
        verdict, score = "no_sla", None
        headline = "No SLA was defined for this run, so it is reported as measured — not passed or failed."

    sla_by_label = {c["label"]: c for c in (sla or {}).get("checks", []) if c["kind"] == "response_time"}
    return {
        "verdict": verdict,
        "score": score,
        "headline": headline,
        "source": report.get("source"),
        "sla": sla or {"defined": False},
        "window": {"start": report.get("start_time"), "end": report.get("end_time")},
        "kpis": {
            "samples": samples,
            "error_pct": round(error_pct, 2),
            "throughput": round(tps, 2) if tps is not None else None,
            "mean_ms": round(mean, 1) if mean is not None else None,
            "median_ms": median,
            "p90_ms": p90,
            "p95_ms": p95,
            "p99_ms": p99,
            "min_ms": _num(total.get("minResTime")),
            "max_ms": _num(total.get("maxResTime")),
            "apdex": apdex_score,
            "apdex_rating": _apdex_rating(apdex_score) if apdex_score is not None else None,
            "apdex_t_ms": t_ms,
            "apdex_f_ms": f_ms,
            "peak_threads": round(peak_threads, 1) if peak_threads is not None else None,
        },
        "findings": findings,
        "transactions": [
            {
                "name": t.get("transaction"),
                "samples": int(_num(t.get("sampleCount")) or 0),
                "errors": int(_num(t.get("errorCount")) or 0),
                "error_pct": round(_num(t.get("errorPct")) or 0, 2),
                "mean_ms": _round(t.get("meanResTime")),
                "median_ms": _round(t.get("medianResTime")),
                "p90_ms": _round(t.get("pct1ResTime")),
                "p95_ms": _round(t.get("pct2ResTime")),
                "p99_ms": _round(t.get("pct3ResTime")),
                "max_ms": _round(t.get("maxResTime")),
                "throughput": _round(t.get("throughput"), 2),
                "status": _txn_status(sla_by_label.get(t.get("transaction"))),
            }
            for t in ranked + [t for t in txns if t not in ranked]
        ],
        "errors": [
            {"type": e[0], "count": int(e[1] or 0), "pct_of_errors": round(e[2] or 0, 1), "pct_of_samples": round(e[3] or 0, 2)}
            for e in errors
        ],
    }


def _round(v: Any, digits: int = 0) -> float | None:
    n = _num(v)
    if n is None:
        return None
    return round(n, digits) if digits else round(n)


def _txn_status(check: dict | None) -> str:
    """'ok' / 'fail' against the SLA's response-time check for this label; 'none' when not covered."""
    if check is None:
        return "none"
    return "ok" if check["passed"] else "fail"


def _overall_series(series: list[dict]) -> list[float]:
    """Average the per-transaction response-time series into one series by timestamp."""
    buckets: dict[float, list[float]] = {}
    for s in series:
        for pt in s.get("data", []):
            if len(pt) > 1 and pt[1] is not None:
                buckets.setdefault(pt[0], []).append(pt[1])
    return [sum(v) / len(v) for _, v in sorted(buckets.items())]


def build_llm_context(analysis: dict[str, Any]) -> str:
    """Compact text version of the analysis for an LLM prompt."""
    k = analysis["kpis"]
    sla = analysis.get("sla") or {}
    if sla.get("defined"):
        verdict = f"Verdict against the user's SLA: {analysis['verdict']} ({sla.get('passed_count')}/{len(sla.get('checks') or [])} checks passed)"
        targets = f"SLA targets: {'; '.join(sla.get('targets') or [])}"
    else:
        verdict = "Verdict: none — no SLA was defined for this run, so it must not be judged pass or fail"
        targets = "SLA targets: none"
    lines = [
        verdict,
        targets,
        f"Samples: {k['samples']}, error rate {k['error_pct']}%, throughput {k['throughput']} req/s",
        f"Response time: mean {_ms(k['mean_ms'])}, median {_ms(k['median_ms'])}, p90 {_ms(k['p90_ms'])}, "
        f"p95 {_ms(k['p95_ms'])}, p99 {_ms(k['p99_ms'])}, max {_ms(k['max_ms'])}",
    ]
    if k.get("apdex") is not None:
        lines.append(f"Apdex: {k['apdex']:.2f} ({k['apdex_rating']}), T={_ms(k['apdex_t_ms'])}, F={_ms(k['apdex_f_ms'])}")
    if k.get("peak_threads") is not None:
        lines.append(f"Peak active threads: {k['peak_threads']}")
    lines.append("\nRule-based findings:")
    lines += [f"- [{f['severity']}] {f['title']}: {f['detail']}" for f in analysis["findings"]]
    lines.append("\nTransactions (slowest p95 first):")
    for t in analysis["transactions"][:15]:
        lines.append(
            f"- {t['name']}: {t['samples']} samples, {t['error_pct']}% errors, mean {_ms(t['mean_ms'])}, "
            f"p95 {_ms(t['p95_ms'])}, p99 {_ms(t['p99_ms'])}"
        )
    if analysis["errors"]:
        lines.append("\nErrors:")
        lines += [f"- {e['type']}: {e['count']} ({e['pct_of_errors']}% of errors)" for e in analysis["errors"]]
    return "\n".join(lines)
