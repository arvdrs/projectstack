"""
Injector health: signs that the load generator, not the system under test, is
the bottleneck.

  * Connect time rising: new connections take much longer (ports, sockets or the
    network of the load generator, or the server's accept queue).
  * Throughput dropping while users stay constant and latency stays flat: in a
    closed workload throughput ≈ users ÷ (response time + think time), so if both
    users and response times hold, the load generator stopped sending (CPU, GC,
    network on the injector).
"""

from __future__ import annotations

from projectstack.results.detectors import Context, Finding
from projectstack.results.detectors.changepoint import change_points, segment_levels


def _median(values):
    s = sorted(values)
    return s[len(s) // 2] if s else 0


def detect_connect(ctx: Context) -> tuple[list[Finding], str | None]:
    if "Connect" not in ctx.columns:
        return [], "the result file has no Connect column (jmeter.save.saveservice.connect_time)"
    pts = ctx.dense_points()
    if len(pts) < 8:
        return [], f"only {len(pts)} timeline buckets — too short for change-point detection"
    values = [p["connect_ms"] for p in pts]
    if max(values) <= 0:
        return [], "Connect time is always 0 (connections are reused), so it cannot be analysed"
    t = ctx.thresholds
    levels = segment_levels(values, change_points(values))
    findings = []
    for prev, cur in zip(levels, levels[1:]):
        before, after = prev[2], cur[2]
        if after - before < t.connect_abs_increase_ms or after < before * (1 + t.connect_rel_increase):
            continue
        start_ms = pts[cur[0]]["t_ms"]
        p95_before = _median([p["p95_ms"] for p in pts[prev[0]:prev[1]]])
        p95_after = _median([p["p95_ms"] for p in pts[cur[0]:cur[1]]])
        findings.append(Finding(
            type="injector_connect", severity="warning",
            title=f"Connect time rose from {before:.0f} ms to {after:.0f} ms at {ctx.offset(start_ms)}",
            detail="New connections took much longer from this point. Typical causes are the load generator "
                   "running short of ports or sockets, network saturation on the injector, or the server's "
                   "connection backlog filling up. Check injector CPU and network, and server connection limits.",
            start_ms=start_ms, end_ms=pts[cur[1] - 1]["t_ms"] + ctx.bucket_ms,
            evidence={"connect_before_ms": round(before, 1), "connect_after_ms": round(after, 1),
                      "p95_before_ms": round(p95_before), "p95_after_ms": round(p95_after),
                      "users": pts[cur[0]]["users"]},
        ))
    return findings, None


def detect_throughput(ctx: Context) -> tuple[list[Finding], str | None]:
    window = ctx.metrics["window"]
    if window["source"] == "full":
        return [], "no steady state, so constant-load throughput cannot be checked"
    pts = [p for p in ctx.dense_points() if window["start_ms"] <= p["t_ms"] and p["t_ms"] + ctx.bucket_ms <= window["end_ms"]]
    if len(pts) < 5:
        return [], f"the steady state spans only {len(pts)} full timeline buckets"
    t = ctx.thresholds
    tps0 = _median([p["tps"] for p in pts])
    avg0 = _median([p["avg_ms"] for p in pts])
    users0 = _median([p["users"] for p in pts])
    flagged = [p["tps"] <= (1 - t.injector_tps_drop) * tps0
               and abs(p["avg_ms"] - avg0) <= t.injector_latency_band * avg0
               and p["users"] >= 0.95 * users0 for p in pts]
    findings, i = [], 0
    while i < len(pts):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(pts) and flagged[j + 1]:
            j += 1
        if j > i:                                  # at least two buckets in a row
            span = pts[i:j + 1]
            tps = _median([p["tps"] for p in span])
            start_ms, end_ms = span[0]["t_ms"], span[-1]["t_ms"] + ctx.bucket_ms
            findings.append(Finding(
                type="injector_throughput", severity="warning",
                title=f"Throughput fell {(1 - tps / tps0) * 100:.0f}% at {ctx.offset(start_ms)} with users and "
                      "response times unchanged",
                detail=f"Throughput dropped from {tps0:,.1f} to {tps:,.1f} req/s for "
                       f"{(end_ms - start_ms) / 60000:.0f} min while {users0:.0f} users stayed active and average "
                       f"response time held at about {avg0:,.0f} ms. The load generator, not the server, is the "
                       "likely bottleneck: check injector CPU, garbage collection and network.",
                start_ms=start_ms, end_ms=end_ms,
                evidence={"tps_typical": round(tps0, 2), "tps_during": round(tps, 2), "avg_ms_typical": round(avg0, 1),
                          "avg_ms_during": round(_median([p["avg_ms"] for p in span]), 1), "users": users0},
            ))
        i = j + 1
    return findings, None
