"""
Saturation: as users increase, throughput stops increasing (or falls) while
latency climbs. Reports the user count at the knee — the last load level at
which throughput still grew with the users.

Load levels come from the timeline buckets up to the point peak load is reached
(ramp-down is ignored). Stepping profiles give one level per step; linear ramps
are binned into levels 5% of peak users wide.
"""

from __future__ import annotations

from projectstack.results.detectors import Context, Finding

MIN_LEVELS = 4


def _median(values):
    s = sorted(values)
    return s[len(s) // 2]


def load_levels(ctx: Context) -> list[dict]:
    pts = ctx.dense_points()
    if not pts:
        return []
    peak = max(p["users"] for p in pts)
    last_peak = max(i for i, p in enumerate(pts) if p["users"] >= 0.95 * peak)
    first_peak = min(i for i, p in enumerate(pts) if p["users"] >= 0.95 * peak)
    # Ramp buckets, plus the buckets held at peak load (but no ramp-down)
    pts = [p for i, p in enumerate(pts) if i <= last_peak and (i < first_peak or p["users"] >= 0.95 * peak)]
    width = max(1.0, 0.05 * peak)
    groups: dict[int, list[dict]] = {}
    for p in pts:
        if p["users"] > 0:
            groups.setdefault(round(p["users"] / width), []).append(p)
    return [{"users": _median([p["users"] for p in g]), "tps": _median([p["tps"] for p in g]),
             "p95_ms": _median([p["p95_ms"] for p in g]), "buckets": len(g), "start_ms": g[0]["t_ms"]}
            for _, g in sorted(groups.items())]


def detect(ctx: Context) -> tuple[list[Finding], str | None]:
    levels = load_levels(ctx)
    if len(levels) < MIN_LEVELS:
        return [], (f"the load went through {len(levels)} level(s); saturation needs a ramp or steps "
                    f"through at least {MIN_LEVELS}")
    t = ctx.thresholds
    eff = [None] + [
        ((b["tps"] - a["tps"]) / a["tps"]) / ((b["users"] - a["users"]) / a["users"])
        if a["tps"] > 0 and b["users"] > a["users"] else None
        for a, b in zip(levels, levels[1:])
    ]
    for i in range(1, len(levels)):
        flat_now = eff[i] is not None and eff[i] < t.saturation_efficiency
        flat_next = i + 1 >= len(levels) or (eff[i + 1] is not None and eff[i + 1] < t.saturation_efficiency)
        if not (flat_now and flat_next):
            continue
        knee = levels[i - 1]
        later = levels[i:]
        p95_after = max(l["p95_ms"] for l in later)
        if p95_after < knee["p95_ms"] * (1 + t.saturation_latency_rise):
            continue        # throughput flat but latency too: limited by the script (think time), not the server
        max_tps = max(l["tps"] for l in levels)
        final = levels[-1]
        dropping = final["tps"] < 0.9 * max_tps
        return [Finding(
            type="saturation",
            severity="critical" if dropping else "warning",
            title=f"Saturation at about {knee['users']:.0f} users: throughput "
                  f"{'fell' if dropping else 'stopped growing'} at {max_tps:,.1f} req/s while p95 climbed",
            detail=(f"Up to {knee['users']:.0f} users throughput grew with the load; from there to "
                    f"{final['users']:.0f} users it {'dropped to' if dropping else 'stayed near'} "
                    f"{final['tps']:,.1f} req/s while p95 went from {knee['p95_ms']:,.0f} ms to "
                    f"{p95_after:,.0f} ms. The system under test reached a capacity limit."),
            start_ms=levels[i]["start_ms"], end_ms=None,
            evidence={"knee_users": knee["users"], "tps_at_knee": round(knee["tps"], 2), "max_tps": round(max_tps, 2),
                      "p95_at_knee": round(knee["p95_ms"]), "p95_after": round(p95_after),
                      "peak_users": final["users"], "tps_at_peak": round(final["tps"], 2),
                      "levels": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in l.items()}
                                 for l in levels]},
        )], None
    return [], None
