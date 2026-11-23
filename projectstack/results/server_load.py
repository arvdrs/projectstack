"""
How the servers followed the load test: server metric series (fetched from AWS / Azure,
or read off screenshots by the AI) set against the test's own timeline — active users,
throughput, p95 and the error rate — minute by minute.

For each server metric, over the points that fall inside the test:

  load     Pearson r with the active users over the whole test, ramp-up included:
           does the metric rise and fall with the load?
  p95      r with the p95 response time over the steady state, where the load is flat,
           so that both simply growing with the users does not count; over the whole
           test (and said so) when the steady state has too few points
  errors   r with the error rate over the test, when the test had errors
  trend    the steady state's last quarter against its first: a metric still climbing
           under flat load (a leak, a filling cache or queue); 10% of its level, or 3
           points for a percentage
  steady   the level during the steady state, and the peak and when it came

Strength: |r| ≥ 0.7 strong, ≥ 0.4 moderate, otherwise weak. A metric that hardly moves
is "flat" and gets no r (a correlation of noise means nothing). The observations are
written from these numbers by rules, so they are the same every time.
"""

from __future__ import annotations

import math
from typing import Any

STRONG, MODERATE = 0.7, 0.4
LINK_P95 = 0.6                  # |r| with p95 or errors worth calling a link
MIN_POINTS = 5
MIN_STEADY = 6
FLAT_SHARE = 0.05               # range under 5% of the peak (or under 1 point for a %) is flat
TREND_SHARE = 0.10              # steady-state change of 10% of the level …
TREND_POINTS = 3.0              # … or of 3 points for a percentage (memory 47% → 52% matters)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, r))


def strength(r: float | None) -> str | None:
    if r is None:
        return None
    a = abs(r)
    return "strong" if a >= STRONG else "moderate" if a >= MODERATE else "weak"


def fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "%":
        return f"{v:.0f}%" if abs(v) >= 10 else f"{v:.1f}%"
    if unit == "ms":
        return f"{v:,.0f} ms"
    if unit in ("bytes", "bytes/s"):
        for size, name in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
            if abs(v) >= size:
                text = f"{v / size:,.1f} {name}"
                break
        else:
            text = f"{v:,.0f} B"
        return f"{text}/s" if unit == "bytes/s" else text
    text = f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.1f}" if abs(v) >= 10 else f"{v:,.2f}"
    return f"{text} {unit}".strip() if unit and unit not in ("count",) else text


def _clock(offset_s: float, start_iso: str) -> str:
    from datetime import datetime, timedelta
    try:
        dt = datetime.fromisoformat(start_iso) + timedelta(seconds=offset_s)
        return dt.strftime("%H:%M UTC")
    except ValueError:
        return f"t+{int(offset_s // 60)}m"


# ---------------------------------------------------------------------------
# Lining the server points up with the test's timeline
# ---------------------------------------------------------------------------

def _test_at(points: list[dict[str, Any]], bucket_s: int, t0: float, t1: float) -> dict[str, float] | None:
    """The test's users, throughput, p95 and error rate over [t0, t1) (or the bucket holding t0 when t1 == t0)."""
    if t1 <= t0:
        inside = [p for p in points if p["t_offset_s"] <= t0 < p["t_offset_s"] + bucket_s]
    else:
        inside = [p for p in points if t0 <= p["t_offset_s"] + bucket_s / 2 < t1]
    inside = [p for p in inside if p.get("samples")]
    if not inside:
        return None
    samples = sum(p["samples"] for p in inside)
    return {"users": sum(p.get("users") or 0 for p in inside) / len(inside),
            "tps": sum(p.get("tps") or 0 for p in inside) / len(inside),
            "p95": sum((p.get("p95_ms") or 0) * p["samples"] for p in inside) / samples,
            "err": 100.0 * sum(p.get("errors") or 0 for p in inside) / samples}


def align(points: list[tuple[float, float]], doc: dict[str, Any], period_s: float = 0) -> list[dict[str, float]]:
    """Each server point inside the test with the test's figures at that time.

    points are (seconds from the run's start, value). A point stands for [t, t + period_s)
    (a CloudWatch minute), or for the instant t when period_s is 0 (a value read off a chart).
    """
    tl = doc.get("timeline") or {}
    test_points = tl.get("points") or []
    bucket = float(tl.get("bucket_s") or 60)
    duration = float(doc["run"].get("duration_s") or 0)
    out = []
    for t, v in sorted(points):
        if v is None or not (-bucket < t <= duration):
            continue
        at = _test_at(test_points, bucket, t, t + period_s)
        if at is not None:
            out.append({"t": t, "v": float(v), **at})
    return out


# ---------------------------------------------------------------------------
# One metric
# ---------------------------------------------------------------------------

def relate_one(series: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any] | None:
    """The relation of one server series to the test; None when too few points fall inside the test."""
    rows = align(series["points"], doc, float(series.get("period_s") or 0))
    if len(rows) < MIN_POINTS:
        return None
    unit = series.get("unit") or ""
    vals = [r["v"] for r in rows]
    lo, hi = min(vals), max(vals)
    flat = (hi - lo) < (1.0 if unit == "%" else FLAT_SHARE * max(abs(hi), abs(lo), 1e-9))
    steady = doc.get("steady") or {}
    s0, s1 = steady.get("start_offset_s"), steady.get("end_offset_s")
    in_steady = [r for r in rows if s0 is not None and s1 is not None and s0 <= r["t"] <= s1]

    def r_of(rs: list[dict[str, float]], key: str) -> float | None:
        return None if flat else pearson([x["v"] for x in rs], [x[key] for x in rs])

    r_users = r_of(rows, "users")
    r_tps = r_of(rows, "tps")
    if len(in_steady) >= MIN_STEADY:
        r_p95, p95_scope = r_of(in_steady, "p95"), "steady"
    else:
        r_p95, p95_scope = r_of(rows, "p95"), "test"
    had_errors = any(r["err"] > 0 for r in rows)
    r_err = r_of(rows, "err") if had_errors else None

    trend = None
    if len(in_steady) >= MIN_STEADY:
        q = max(2, len(in_steady) // 4)
        first = sum(x["v"] for x in in_steady[:q]) / q
        last = sum(x["v"] for x in in_steady[-q:]) / q
        change = last - first
        level = max(abs(first), abs(last), 1e-9)
        if (abs(change) >= TREND_POINTS) if unit == "%" else (abs(change) >= TREND_SHARE * level):
            trend = {"direction": "rising" if change > 0 else "falling", "from": round(first, 2), "to": round(last, 2)}

    peak = max(rows, key=lambda x: x["v"])
    steady_vals = [x["v"] for x in in_steady]
    return {
        "key": series.get("key") or series["name"], "name": series["name"], "unit": unit,
        "source": series.get("source", ""), "points": len(rows),
        "flat": flat, "low": round(lo, 2), "high": round(hi, 2),
        "steady_avg": round(sum(steady_vals) / len(steady_vals), 2) if steady_vals else None,
        "peak": round(peak["v"], 2), "peak_offset_s": round(peak["t"]),
        "r_users": _round(r_users), "r_tps": _round(r_tps), "r_p95": _round(r_p95), "p95_scope": p95_scope,
        "r_errors": _round(r_err), "load": strength(r_users), "with_p95": strength(r_p95),
        "with_errors": strength(r_err), "trend": trend,
        "_points": [[round(x["t"]), round(x["v"], 3)] for x in rows],
    }


def _round(r: float | None) -> float | None:
    return None if r is None else round(r, 2)


# ---------------------------------------------------------------------------
# All metrics, and what they say
# ---------------------------------------------------------------------------

def _p95_moved(doc: dict[str, Any]) -> bool:
    """Whether the steady state's p95 varied enough for a relation to mean something."""
    steady = doc.get("steady") or {}
    s0, s1 = steady.get("start_offset_s"), steady.get("end_offset_s")
    pts = [p.get("p95_ms") for p in (doc.get("timeline") or {}).get("points") or []
           if p.get("samples") and p.get("p95_ms") is not None and (s0 is None or s0 <= p["t_offset_s"] <= (s1 or 0))]
    if len(pts) < 3:
        return False
    mean = sum(pts) / len(pts)
    sd = math.sqrt(sum((x - mean) ** 2 for x in pts) / len(pts))
    return mean > 0 and sd / mean >= 0.05


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _with_r(rows: list[dict[str, Any]], key: str) -> str:
    """"CPU · app-1 (r 0.91) and Memory · app-1 (r 0.62)" """
    return _join(["%s (r %.2f%s)" % (r["name"], r[key], ", in the opposite direction" if r[key] < 0 else "")
                  for r in rows])


def _range(r: dict[str, Any]) -> str:
    low, high = fmt_value(r["low"], r["unit"]), fmt_value(r["high"], r["unit"])
    if low == high:
        return "%s stayed at about %s" % (r["name"], low)
    return "%s stayed between %s and %s" % (r["name"], low, high)


def observations(rows: list[dict[str, Any]], doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    peak_users = doc["run"].get("peak_users")
    at_users = " at %s users" % format(peak_users, ",") if peak_users else ""
    # A metric still climbing under flat load did not "hold steady": the trend sentence covers it
    follow = [r for r in rows if r["load"] == "strong" and (r["r_users"] or 0) > 0 and not r["trend"]]
    partly = [r for r in rows if r["load"] == "moderate" and (r["r_users"] or 0) > 0 and not r["trend"]]
    apart = [r for r in rows if r["flat"] or r["load"] == "weak" or (r["r_users"] or 0) < 0]

    # 1. Which server metrics followed the load
    if follow:
        levels = ["%s (r %.2f, about %s%s)" % (r["name"], r["r_users"],
                                               fmt_value(r["steady_avg"] if r["steady_avg"] is not None else r["peak"], r["unit"]),
                                               at_users) for r in follow[:4]]
        out.append("%s followed the load: %s rose with the users through the ramp-up and held steady with them."
                   % (_join(levels), "it" if len(follow) == 1 else "they"))
    if partly:
        out.append("%s partly followed the load." % _with_r(partly[:4], "r_users"))
    if apart:
        bits = [_range(r) if r["flat"] or r["r_users"] is None else "%s (r %.2f)" % (r["name"], r["r_users"])
                for r in apart[:4]]
        out.append("%s: no clear link with the load." % _join(bits))

    # 2. Response times: over the steady state, where the load is flat
    linked = sorted([r for r in rows if r["r_p95"] is not None and abs(r["r_p95"]) >= LINK_P95],
                    key=lambda r: -abs(r["r_p95"]))
    scope = ("During the steady state, while the load was flat," if all(r["p95_scope"] == "steady" for r in rows)
             else "Over the test,")
    if linked:
        top = linked[0]
        way = "rose and fell together" if top["r_p95"] > 0 else "moved in opposite directions"
        out.append("%s the p95 response time and %s %s (r %.2f): this server metric is the likeliest place "
                   "to look for what slowed responses." % (scope, top["name"], way, top["r_p95"]))
        if len(linked) > 1:
            out.append("Response times also moved with %s." % _with_r(linked[1:4], "r_p95"))
    elif _p95_moved(doc):
        tried = [r for r in rows if r["r_p95"] is not None]
        if tried:
            best = max(tried, key=lambda r: abs(r["r_p95"]))
            out.append("%s the response-time changes the test saw did not follow any server metric shown "
                       "(the closest is %s, r %.2f), so these servers do not explain them; look at other tiers "
                       "or the application itself." % (scope, best["name"], best["r_p95"]))
    else:
        out.append("Response times were steady during the steady state, so there was no slowdown to relate "
                   "to the servers.")

    # 3. Errors
    had_errors = any((p.get("errors") or 0) > 0 for p in (doc.get("timeline") or {}).get("points") or [])
    err_linked = sorted([r for r in rows if r["r_errors"] is not None and r["r_errors"] >= LINK_P95],
                        key=lambda r: -r["r_errors"])
    err_partly = sorted([r for r in rows if r["r_errors"] is not None and MODERATE <= r["r_errors"] < LINK_P95],
                        key=lambda r: -r["r_errors"])
    if err_linked:
        out.append("The error rate rose together with %s." % _with_r(err_linked[:3], "r_errors"))
    elif err_partly:
        out.append("The errors partly coincided with %s." % _with_r(err_partly[:3], "r_errors"))
    elif had_errors:
        out.append("The errors the test saw did not coincide with any server metric shown.")

    # 4. Still climbing under flat load
    for r in [r for r in rows if r["trend"]][:3]:
        t = r["trend"]
        span = "about %s to %s" % (fmt_value(t["from"], r["unit"]), fmt_value(t["to"], r["unit"]))
        with_load = r["load"] in ("strong", "moderate") and (r["r_users"] or 0) > 0
        if t["direction"] == "rising":
            out.append("%s %skept rising during the steady state although the load was flat (%s): worth "
                       "watching on a longer test (a leak, a growing cache or a queue)."
                       % (r["name"], "rose with the load and " if with_load else "", span))
        else:
            out.append("%s fell during the steady state although the load was flat (%s)." % (r["name"], span))

    # 5. Headroom on the busiest percentage metric
    busy = [r for r in rows if r["unit"] == "%" and not r["flat"]]
    if busy:
        top = max(busy, key=lambda r: r["peak"])
        if top["peak"] >= 85:
            out.append("%s reached %s at its peak, close to its limit." % (top["name"], fmt_value(top["peak"], "%")))
        elif top["peak"] < 70:
            out.append("The busiest of these, %s, peaked at about %s, so the servers shown had headroom at this "
                       "load." % (top["name"], fmt_value(top["peak"], "%")))
    return out


def relate(series: list[dict[str, Any]], doc: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    """Rows for every series with enough points inside the test, and the observations; None when none qualify.

    series: [{key, name, unit, points: [(seconds from the run's start, value)], period_s}]
    """
    rows = [r for r in (relate_one({**s, "source": source}, doc) for s in series) if r is not None]
    if not rows:
        return None
    # The points compared, for drawing the server lines under the test's graph (seconds from the start, value)
    lines = [{"key": r["key"], "name": r["name"], "unit": r["unit"], "points": r.pop("_points")} for r in rows]
    return {"source": source, "rows": rows, "observations": observations(rows, doc), "lines": lines,
            "thresholds": {"strong": STRONG, "moderate": MODERATE}}


def from_cloud(data: dict[str, Any] | None, doc: dict[str, Any]) -> dict[str, Any] | None:
    """The relation for metrics fetched from AWS / Azure (server.json)."""
    if not data:
        return None
    series = [{"key": f"{s.get('resource_id')}|{s.get('metric')}", "name": f"{s.get('label')} · {s.get('resource')}",
               "unit": s.get("unit") or "", "period_s": s.get("period_s") or 60,
               "points": [(p["t_offset_s"], p["value"]) for p in s.get("points") or [] if p.get("value") is not None]}
              for s in data.get("series") or []]
    return relate(series, doc, source="cloud")
