"""
Change points in p95 latency and error rate: the bucket where a lasting
degradation starts, the level before and after, and the labels that changed most.
"""

from __future__ import annotations

from projectstack.results.detectors import Context, Finding
from projectstack.results.detectors.changepoint import change_points, segment_levels

MIN_BUCKETS = 8


def _segments(ctx: Context, key: str):
    pts = ctx.dense_points()
    if len(pts) < MIN_BUCKETS:
        return None, f"only {len(pts)} timeline buckets — too short for change-point detection (needs {MIN_BUCKETS})"
    values = [p[key] for p in pts]
    return (pts, segment_levels(values, change_points(values))), None


def _window(ctx: Context, pts: list[dict], seg: tuple[int, int, float]) -> tuple[int, int]:
    return pts[seg[0]]["t_ms"], pts[seg[1] - 1]["t_ms"] + ctx.bucket_ms


def _median(values: list[float]) -> float:
    s = sorted(values)
    return s[len(s) // 2] if s else 0.0


def detect_latency(ctx: Context) -> tuple[list[Finding], str | None]:
    res, why = _segments(ctx, "p95_ms")
    if res is None:
        return [], why
    pts, levels = res
    t = ctx.thresholds
    findings = []
    for prev, cur in zip(levels, levels[1:]):
        before, after = prev[2], cur[2]
        if after < before * (1 + t.latency_rel_increase) or after - before < t.latency_abs_increase_ms:
            continue
        change_ms, end_ms = _window(ctx, pts, cur)
        prev_start, _ = _window(ctx, pts, prev)
        labels = _labels_changed(ctx, prev_start, change_ms, end_ms, "latency")
        users_before = _median([p["users"] for p in pts[prev[0]:prev[1]]])
        users_after = _median([p["users"] for p in pts[cur[0]:cur[1]]])
        load = ("while the load stayed at about {:.0f} users".format(users_after)
                if abs(users_after - users_before) <= max(1, 0.05 * users_before)
                else f"as users went from {users_before:.0f} to {users_after:.0f}")
        findings.append(Finding(
            type="latency_change",
            severity="critical" if after >= 2 * before else "warning",
            title=f"p95 rose from {before:,.0f} ms to {after:,.0f} ms at {ctx.offset(change_ms)}",
            detail=f"Response times stepped up {(after / before - 1) * 100:.0f}% {load}"
                   + (f"; most affected: {', '.join(l['label'] for l in labels[:3])}" if labels else "") + ".",
            start_ms=change_ms, end_ms=end_ms, labels=[l["label"] for l in labels],
            evidence={"metric": "p95_ms", "before": round(before), "after": round(after),
                      "increase_pct": round((after / before - 1) * 100, 1), "users_before": users_before,
                      "users_after": users_after,
                      "tps_before": round(_median([p["tps"] for p in pts[prev[0]:prev[1]]]), 2),
                      "tps_after": round(_median([p["tps"] for p in pts[cur[0]:cur[1]]]), 2),
                      "labels": labels},
        ))
    return findings, None


def detect_error_rate(ctx: Context) -> tuple[list[Finding], str | None]:
    res, why = _segments(ctx, "error_pct")
    if res is None:
        return [], why
    pts, levels = res
    t = ctx.thresholds
    findings = []
    for prev, cur in zip(levels, levels[1:]):
        before, after = prev[2], cur[2]
        if after - before < t.error_rate_abs_increase or after < before * (1 + t.error_rate_rel_increase):
            continue
        change_ms, end_ms = _window(ctx, pts, cur)
        if (end_ms - change_ms) < t.burst_max_share * (ctx.bounds["end_ms"] - ctx.bounds["start_ms"]) \
                and cur is not levels[-1]:
            continue          # short and recovered: reported as an error burst instead
        prev_start, _ = _window(ctx, pts, prev)
        labels = _labels_changed(ctx, prev_start, change_ms, end_ms, "errors")
        findings.append(Finding(
            type="error_rate_change",
            severity="critical" if after >= 5 else "warning",
            title=f"Error rate rose from {before:.1f}% to {after:.1f}% at {ctx.offset(change_ms)}",
            detail="Failures increased and stayed high"
                   + (f"; most affected: {', '.join(l['label'] for l in labels[:3])}" if labels else "") + ".",
            start_ms=change_ms, end_ms=end_ms, labels=[l["label"] for l in labels],
            evidence={"metric": "error_pct", "before": round(before, 2), "after": round(after, 2),
                      "users_at_change": pts[cur[0]]["users"], "labels": labels},
        ))
    return findings, None


def _labels_changed(ctx: Context, start_ms: int, change_ms: int, end_ms: int, what: str) -> list[dict]:
    """Labels whose p95 (or error rate) changed most between [start, change) and [change, end)."""
    db = ctx.db
    if what == "latency":
        rows = db.rows(
            f"""WITH fromUnixTimestamp64Milli({{c:Int64}}) AS c
                SELECT label, quantileExactIf(0.95)(elapsed, ts < c) AS before,
                       quantileExactIf(0.95)(elapsed, ts >= c) AS after,
                       countIf(ts < c) AS nb, countIf(ts >= c) AS na
                FROM {db.db}.samples
                WHERE run_id = {{run:String}} AND has({{req:Array(String)}}, label)
                  AND ts >= fromUnixTimestamp64Milli({{s:Int64}}) AND ts < fromUnixTimestamp64Milli({{e:Int64}})
                GROUP BY label HAVING nb >= 10 AND na >= 10 AND after > before
                ORDER BY after / greatest(before, 1) DESC LIMIT 5""",
            {"run": ctx.run_id, "req": ctx.requests, "s": start_ms, "c": change_ms, "e": end_ms},
        )
        return [{"label": r[0], "p95_before": int(r[1]), "p95_after": int(r[2])} for r in rows]
    rows = db.rows(
        f"""WITH fromUnixTimestamp64Milli({{c:Int64}}) AS c
            SELECT label, countIf(NOT success AND ts < c) / greatest(countIf(ts < c), 1) * 100 AS before,
                   countIf(NOT success AND ts >= c) / greatest(countIf(ts >= c), 1) * 100 AS after
            FROM {db.db}.samples
            WHERE run_id = {{run:String}} AND has({{req:Array(String)}}, label)
              AND ts >= fromUnixTimestamp64Milli({{s:Int64}}) AND ts < fromUnixTimestamp64Milli({{e:Int64}})
            GROUP BY label HAVING after > before ORDER BY after - before DESC LIMIT 5""",
        {"run": ctx.run_id, "req": ctx.requests, "s": start_ms, "c": change_ms, "e": end_ms},
    )
    return [{"label": r[0], "error_pct_before": round(r[1], 2), "error_pct_after": round(r[2], 2)} for r in rows]
