"""SLA breaches, judged over the steady-state window only."""

from __future__ import annotations

from projectstack.analyzer.sla import evaluate_sla
from projectstack.results.detectors import Context, Finding

_UNITS = {"ms": " ms", "%": "%", "req/s": " req/s"}


def detect(ctx: Context) -> tuple[list[Finding], str | None]:
    if not ctx.sla:
        ctx.extra["sla_result"] = {"defined": False}
        return [], "no SLA given (--sla), so the run is not judged pass or fail"
    by_label = {}
    for kind in ("request", "transaction"):
        for s in ctx.metrics["labels"][kind]:
            by_label[s["label"]] = {"total": s["samples"], "failed": s["errors"], "avg_ms": s["avg_ms"],
                                    "p90_ms": s["p90_ms"], "p95_ms": s["p95_ms"], "p99_ms": s["p99_ms"]}
    window = ctx.metrics["window"]
    result = evaluate_sla(ctx.sla, by_label, window["duration_s"],
                          transaction_labels=[s["label"] for s in ctx.metrics["labels"]["transaction"]])
    result["window"] = {"start_ms": window["start_ms"], "end_ms": window["end_ms"], "source": window["source"]}
    ctx.extra["sla_result"] = result

    findings = []
    for c in result["checks"]:
        if c["passed"]:
            continue
        unit = _UNITS[c["unit"]]
        cmp = "<" if c["kind"] == "throughput" else ">"
        what = f"{c['label']} {c['metric']}" if c["kind"] == "response_time" else c["kind"].replace("_", " ")
        actual = "not measured" if c["actual"] is None else f"{c['actual']:,.10g}{unit}"
        findings.append(Finding(
            type="sla_breach", severity="critical",
            title=f"SLA breach: {what} {actual} {cmp} {c['target']:,.10g}{unit}",
            detail=c.get("note") or f"Measured over the steady state ({window['source']} window).",
            start_ms=window["start_ms"], end_ms=window["end_ms"], labels=[c["label"]] if c["label"] else [],
            evidence={k: c[k] for k in ("kind", "metric", "target", "actual", "unit")},
        ))
    return findings, None
