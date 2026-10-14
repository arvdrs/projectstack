"""Plain-text summary tables for the terminal."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from projectstack.analyzer.sla import describe

_COLS = [("Samples", "samples", "{:,}"), ("Avg", "avg_ms", "{:,.0f}"), ("p50", "p50_ms", "{:,}"),
         ("p90", "p90_ms", "{:,}"), ("p95", "p95_ms", "{:,}"), ("p99", "p99_ms", "{:,}"), ("Max", "max_ms", "{:,}"),
         ("Err%", "error_pct", "{:.2f}%"), ("TPS", "tps", "{:,.2f}")]
LABEL_W = 36


def duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    mi, sec = divmod(rem, 60)
    return f"{h}h {mi:02d}m" if h else f"{mi}m {sec:02d}s" if mi else f"{sec}s"


def clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _table(title: str, rows: list[dict], total: dict | None, total_label: str, limit: int | None) -> list[str]:
    widths = [max(len(h), 8) for h, _, _ in _COLS]
    head = f"{title:<{LABEL_W}} " + " ".join(f"{h:>{w}}" for (h, _, _), w in zip(_COLS, widths))
    lines = [head, "─" * len(head)]
    shown = rows if limit is None else rows[:limit]
    for r in shown:
        lines.append(f"{_cut(r['label'], LABEL_W):<{LABEL_W}} "
                     + " ".join(f"{fmt.format(r[k]):>{w}}" for (_, k, fmt), w in zip(_COLS, widths)))
    if limit is not None and len(rows) > limit:
        lines.append(f"… {len(rows) - limit} more (pstack runs show <run> --all)")
    if total:
        lines.append("─" * len(head))
        lines.append(f"{total_label:<{LABEL_W}} "
                     + " ".join(f"{fmt.format(total[k]):>{w}}" for (_, k, fmt), w in zip(_COLS, widths)))
    return lines


_SEV = {"critical": "✖ critical", "warning": "▲ warning ", "info": "• info    "}
_VERDICT = {"pass": "PASS — SLA met, no problems found", "at_risk": "AT RISK — SLA met, but problems were found",
            "fail": "FAIL — SLA breached", "no_sla": "not judged (no SLA given; use --sla)"}


def render_findings(findings: dict[str, Any]) -> list[str]:
    out = [f"Verdict: {_VERDICT.get(findings['verdict'], findings['verdict'])}"]
    items = findings.get("findings", [])
    if items:
        out.append(f"Findings ({len(items)})")
        for f in items:
            out.append(f"  {_SEV.get(f['severity'], f['severity'])}  {f['title']}")
            out.append(f"               {f['detail']}")
    else:
        out.append("Findings: none — no SLA breaches, change points, saturation, error bursts or injector problems")
    for sk in findings.get("skipped", []):
        if sk["detector"] != "sla":
            out.append(f"  (not checked: {sk['detector'].replace('_', ' ')} — {sk['reason']})")
    base = findings.get("baseline")
    if base and not base.get("found"):
        out.append(f"Baseline: none — {base.get('note', '')}")
    elif base:
        load = base["load"]
        tag = f" ({base['tag']})" if base.get("tag") else ""
        state = "load comparable" if load["comparable"] else f"load NOT comparable ({load['reason']})"
        out.append(f"Baseline: {base['run_id']}{tag} — {state}; "
                   f"{base['summary']['regressed']} regressed, {base['summary']['improved']} improved")
    return out


def _signed(v: float | None, unit: str = "", decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:+,.{decimals}f}{unit}" if abs(v) < 100 else f"{v:+,.0f}{unit}"


def _run_line(name: str, info: dict[str, Any]) -> str:
    tag = f"  ({info['tag']})" if info.get("tag") else ""
    return f"{name:<10}{info['run_id']}{tag}"


def render_comparison(comp: dict[str, Any], limit: int | None = 25) -> str:
    """Terminal view of a comparison: the load, then each kind's labels, baseline → current."""
    load = comp["load"]
    state = "comparable" if load["comparable"] else f"NOT comparable: {load['reason']}"
    lines = [
        _run_line("Baseline", comp["baseline"]) + f"  verdict {comp['baseline'].get('verdict') or '—'}",
        _run_line("Current", comp["current"]),
        f"{'Load':<10}{load['baseline_users']:g} → {load['current_users']:g} users, "
        f"{load['baseline_tps']:,.1f} → {load['current_tps']:,.1f} {load['kind']}s/s — {state}",
        "",
    ]
    head = f"{'Samples':>17} {'p95 (ms)':>17} {'Δ p95':>8} {'Err %':>15} {'Δ err':>8}  Status"
    status_text = {"same": "", "too_few_samples": "too few samples"}
    for kind, data in comp["kinds"].items():
        rows = sorted(data["labels"], key=lambda r: (r["status"] != "regressed", -(r["p95_change_pct"] or 0)))
        title = f"{kind.capitalize()}s"
        lines += [f"{title:<{LABEL_W}} {head}", "─" * (LABEL_W + 1 + len(head))]
        shown = rows if limit is None else rows[:limit]
        overall = [data["overall"]] if data["overall"] else []
        for r in shown + overall:
            if overall and r is overall[0]:
                lines.append("─" * (LABEL_W + 1 + len(head)))
            b, c = r["baseline"], r["current"]
            if r["status"] == "regressed":
                status = "✖ " + ", ".join(r["regressed"])
            elif r["status"] == "improved":
                status = "✓ " + ", ".join(r["improved"])
            else:
                status = status_text[r["status"]]
            lines.append(
                f"{_cut(r['label'], LABEL_W):<{LABEL_W}} {b['samples'] or 0:>8,}→{c['samples'] or 0:<8,} "
                f"{b['p95_ms'] or 0:>8,}→{c['p95_ms'] or 0:<8,} {_signed(r['p95_change_pct'], '%'):>8} "
                f"{b['error_pct'] or 0:>7.2f}→{c['error_pct'] or 0:<7.2f} {_signed(r['err_change_pp'], 'pt', 2):>8}  {status}")
        if limit is not None and len(rows) > limit:
            lines.append(f"… {len(rows) - limit} more (--all)")
        for which, key in (("only in the baseline", "only_in_baseline"), ("new in this run", "only_in_current")):
            names = comp[key].get(kind) or []
            if names:
                more = " …" if len(names) > 5 else ""
                lines.append(f"  {len(names)} label(s) {which}: {', '.join(names[:5])}{more}")
        lines.append("")
    summ, th = comp["summary"], comp["thresholds"]
    few = f", {summ['too_few_samples']} with too few samples" if summ["too_few_samples"] else ""
    lines.append(f"{summ['regressed']} regressed, {summ['improved']} improved, {summ['compared']} compared{few} "
                 f"(regression: p95 +{th['p95_pct']:g}% and +{th['p95_ms']:g} ms, or errors +{th['err_pp']:g} pt)")
    if summ["regressed"] and not load["comparable"]:
        lines.append("These regressions are not counted: the load differed.")
    return "\n".join(lines)


def render(meta: dict[str, Any], metrics: dict[str, Any], stats_mode: str = "both",
           limit: int | None = 25, findings: dict[str, Any] | None = None) -> str:
    run = meta["run"]
    w = metrics["window"]
    out = [
        f"Run {meta['run_id']}   scenario: {meta['scenario']}" + (f"   tag: {meta['tag']}" if meta.get("tag") else ""),
        f"{run['samples']:,} samples · {duration(run['duration_s'])} "
        f"({datetime.fromtimestamp(run['start_ms'] / 1000):%Y-%m-%d %H:%M:%S} → {clock(run['end_ms'])}) · "
        f"peak {run['peak_users']} users ({meta['columns']['users_source']})",
    ]
    steady = meta.get("steady", {})
    if w["source"] == "full":
        out.append("Window: full run — " + (steady.get("note") or "ramp-up and ramp-down included")
                   + " (use --steady START:END to set one)")
    elif w["source"] == "auto":
        out.append(f"Steady state (auto): {clock(w['start_ms'])} → {clock(w['end_ms'])} ({duration(w['duration_s'])}, "
                   f"{steady.get('plateau_users')} users from {steady.get('method')}) — stats below cover this window only")
    else:
        out.append(f"Window: {clock(w['start_ms'])} → {clock(w['end_ms'])} ({w['source']}, "
                   f"{duration(w['duration_s'])}) — stats below cover this window only")
    labels = meta.get("labels", {})
    if labels.get("subresult"):
        out.append(f"{labels['subresult']} redirect/sub-result label(s) excluded to avoid double counting")
    out.append("")

    if stats_mode in ("both", "child") and metrics["labels"]["request"]:
        out += _table("Requests (ms)", metrics["labels"]["request"], metrics["overall"].get("request"),
                      "All requests", limit)
        out.append("")
    if stats_mode in ("both", "parent") and metrics["labels"]["transaction"]:
        out += _table("Transactions (ms)", metrics["labels"]["transaction"], metrics["overall"].get("transaction"),
                      "All transactions", limit)
        out.append("")
    elif stats_mode == "parent":
        out += ["No Transaction Controller samples in this run.", ""]

    errors = metrics.get("errors") or []
    if errors:
        out.append("Top errors (full run, requests)")
        for e in errors[:8]:
            msg = e["failure"] or e["response_message"]
            out.append(f"  {e['count']:>7,}×  {_cut(e['label'], 32):<32}  {e['response_code']:<5} "
                       f"{_cut(' '.join(msg.split()), 60)}   {clock(e['first_ms'])}–{clock(e['last_ms'])}")
        out.append("")

    if findings:
        out += render_findings(findings)
        out.append("")

    ing = meta["ingest"]
    rate = ing["rows"] / ing["seconds"] if ing["seconds"] else 0
    out.append(f"Loaded {ing['rows']:,} rows in {ing['seconds']:.1f}s ({rate:,.0f} rows/s) · "
               f"metrics {meta['timings'].get('metrics_s', 0):.1f}s · ClickHouse {meta['clickhouse']}")
    for wmsg in ing.get("warnings", []):
        out.append(f"Warning: {wmsg}")
    if meta.get("sla"):
        out.append(f"SLA: {'; '.join(describe(meta['sla']))} (judged over the analysis window)")
    return "\n".join(out)


def render_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "No runs yet — analyse one with: pstack analyze results.jtl"
    head = (f"{'Run':<34} {'Scenario':<18} {'Tag':<12} {'Samples':>11} {'Duration':>9} {'Users':>6} {'p95':>7} "
            f"{'Err%':>7} {'TPS':>8}  Verdict")
    lines = [head, "─" * len(head)]
    for r in runs:
        s = r["summary"]
        lines.append(
            f"{r['run_id']:<34} {_cut(r['scenario'], 18):<18} {_cut(r['tag'] or '—', 12):<12} {r['samples']:>11,} "
            f"{duration((r['end_ms'] - r['start_ms']) / 1000):>9} {r['peak_threads']:>6} "
            f"{s.get('p95_ms', 0):>7,} {s.get('error_pct', 0):>6.2f}% {s.get('tps', 0):>8,.2f}  "
            f"{ {'no_sla': 'no SLA', 'unknown': '—'}.get(r.get('verdict', 'unknown'), r.get('verdict')) }"
        )
    return "\n".join(lines)
