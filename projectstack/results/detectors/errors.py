"""Error bursts: short windows where the error rate spikes, with the dominant codes and messages."""

from __future__ import annotations

from projectstack.results.detectors import Context, Finding


def detect_bursts(ctx: Context) -> tuple[list[Finding], str | None]:
    pts = ctx.dense_points()
    if len(pts) < 3:
        return [], "too few timeline buckets"
    t = ctx.thresholds
    rates = sorted(p["error_pct"] for p in pts)
    typical = rates[len(rates) // 2]
    limit = max(typical * t.burst_factor, typical + t.burst_min_increase)
    flagged = [p["error_pct"] >= limit and p["errors"] >= t.burst_min_errors for p in pts]

    windows, i = [], 0
    while i < len(pts):
        if flagged[i]:
            j = i
            while j + 1 < len(pts) and flagged[j + 1]:
                j += 1
            windows.append((i, j))
            i = j + 1
        else:
            i += 1

    run_ms = ctx.bounds["end_ms"] - ctx.bounds["start_ms"]
    findings = []
    for a, b in windows:
        start_ms, end_ms = pts[a]["t_ms"], pts[b]["t_ms"] + ctx.bucket_ms
        if end_ms - start_ms > t.burst_max_share * run_ms:
            continue                       # a lasting change, reported by the error-rate change detector
        span = pts[a:b + 1]
        errors = sum(p["errors"] for p in span)
        samples = sum(p["samples"] for p in span)
        peak = max(p["error_pct"] for p in span)
        causes = _causes(ctx, start_ms, end_ms)
        labels = _labels(ctx, start_ms, end_ms)
        top = causes[0] if causes else None
        findings.append(Finding(
            type="error_burst",
            severity="critical" if peak >= 20 else "warning",
            title=f"Error burst at {ctx.offset(start_ms)} for {(end_ms - start_ms) / 60000:.0f} min: "
                  f"up to {peak:.1f}% failed" + (f", mostly {top['code']} {top['message']}" if top else ""),
            detail=f"{errors:,} of {samples:,} requests failed in this window, against a typical error rate of "
                   f"{typical:.2f}%; the rate returned to normal afterwards.",
            start_ms=start_ms, end_ms=end_ms, labels=[l["label"] for l in labels],
            evidence={"errors": errors, "samples": samples, "peak_error_pct": round(peak, 2),
                      "typical_error_pct": round(typical, 2), "causes": causes, "labels": labels},
        ))
    return findings, None


def _causes(ctx: Context, start_ms: int, end_ms: int) -> list[dict]:
    db = ctx.db
    rows = db.rows(
        f"""SELECT response_code, if(failure_message != '', substring(failure_message, 1, 120), response_message) AS msg,
                   count() AS n
            FROM {db.db}.samples
            WHERE run_id = {{run:String}} AND NOT success AND has({{req:Array(String)}}, label)
              AND ts >= fromUnixTimestamp64Milli({{s:Int64}}) AND ts < fromUnixTimestamp64Milli({{e:Int64}})
            GROUP BY response_code, msg ORDER BY n DESC LIMIT 3""",
        {"run": ctx.run_id, "req": ctx.requests, "s": start_ms, "e": end_ms},
    )
    return [{"code": r[0], "message": " ".join(r[1].split()), "count": int(r[2])} for r in rows]


def _labels(ctx: Context, start_ms: int, end_ms: int) -> list[dict]:
    db = ctx.db
    rows = db.rows(
        f"""SELECT label, countIf(NOT success) AS e, count() AS n
            FROM {db.db}.samples
            WHERE run_id = {{run:String}} AND has({{req:Array(String)}}, label)
              AND ts >= fromUnixTimestamp64Milli({{s:Int64}}) AND ts < fromUnixTimestamp64Milli({{e:Int64}})
            GROUP BY label HAVING e > 0 ORDER BY e DESC LIMIT 5""",
        {"run": ctx.run_id, "req": ctx.requests, "s": start_ms, "e": end_ms},
    )
    return [{"label": r[0], "errors": int(r[1]), "error_pct": round(r[1] / r[2] * 100, 2)} for r in rows]
