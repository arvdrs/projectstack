"""
The report's timeline chart: four small multiples on one time axis (active
users, throughput, p95, error rate), each with its own y-axis, rather than one
chart with two scales. Findings with a time window are shaded across all four
panels and numbered like the findings list; the steady-state window is shaded
in grey. Colours come from CSS custom properties in the report template, so
dark mode is the template's job. A small script adds a crosshair and tooltip;
the same values are in the table under the chart.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

W = 760
LEFT, RIGHT = 56, 14
PANEL_H, GAP, TOP = 84, 34, 50     # TOP leaves a row for band labels above the first panel title
AXIS_H = 26

PANELS = [  # key in the data, title, y floor for an all-zero series
    ("users", "Active users", 1),
    ("tps", "Throughput (requests/s)", 1),
    ("p95", "p95 response time (ms)", 10),
    ("err", "Error rate (%)", 1),
]


def _nice(v: float, floor: float) -> float:
    v = max(v, floor)
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * exp >= v * 1.04:
            return m * exp
    return 10 * exp


def _tick(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:g}K"
    return f"{v:g}" if v == int(v) or v >= 10 else f"{v:.2g}"


def _minutes(seconds: float, step_min: int) -> str:
    m = round(seconds / 60)
    return f"{m // 60}h" if step_min >= 60 else f"{m}m"


def series(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Plot points: bucket midpoints, with the values the tooltip and table show."""
    tl = doc.get("timeline", {})
    bucket = tl.get("bucket_s", 60)
    start = doc.get("run", {}).get("start")
    try:
        t0 = datetime.fromisoformat(start).astimezone(timezone.utc) if start else None
    except ValueError:
        t0 = None
    out = []
    for p in tl.get("points", []):
        t = p["t_offset_s"]
        shown = round(t / 10) * 10 if bucket >= 60 else t
        out.append({"t": t + bucket / 2, "start": max(0, shown), "users": p["users"], "tps": p["tps"], "p95": p["p95_ms"],
                    "err": p["error_pct"], "samples": p["samples"],
                    "clock": (t0 + timedelta(seconds=t)).strftime("%H:%M:%S") if t0 else ""})
    return out


def timeline_svg(doc: dict[str, Any], uid: str = "tl") -> str:
    pts = series(doc)
    if not pts:
        return ""
    bucket = doc.get("timeline", {}).get("bucket_s", 60)
    x0 = min(0.0, pts[0]["start"])
    x1 = max(float(doc.get("run", {}).get("duration_s") or 0), pts[-1]["start"] + bucket)
    plot_w = W - LEFT - RIGHT

    def x(t: float) -> float:
        return LEFT + (t - x0) / ((x1 - x0) or 1) * plot_w

    height = TOP + len(PANELS) * PANEL_H + (len(PANELS) - 1) * GAP + AXIS_H
    parts: list[str] = []
    bands = _bands(doc, x, x0, x1)

    for k, (key, title, floor) in enumerate(PANELS):
        y0 = TOP + k * (PANEL_H + GAP)
        top = _nice(max(p[key] for p in pts), floor)

        def y(v: float) -> float:
            return y0 + PANEL_H - (v / top) * PANEL_H

        g = [f'<text class="tl-title" x="{LEFT}" y="{y0 - 10}">{escape(title)}</text>']
        for frac in (0, 0.5, 1):
            yy = y(top * frac)
            g.append(f'<line class="tl-{"base" if frac == 0 else "grid"}" x1="{LEFT}" x2="{W - RIGHT}" '
                     f'y1="{yy:.1f}" y2="{yy:.1f}"/>')
            g.append(f'<text class="tl-tick" x="{LEFT - 8}" y="{yy + 4:.1f}" text-anchor="end">{_tick(top * frac)}</text>')
        for b in bands:
            g.append(f'<rect class="tl-band {b["cls"]}" x="{b["x"]:.1f}" y="{y0}" width="{b["w"]:.1f}" '
                     f'height="{PANEL_H}"/>')
        if key == "err":
            target = _error_target(doc)
            if target is not None and target <= top:
                yy = y(target)
                g.append(f'<line class="tl-ref" x1="{LEFT}" x2="{W - RIGHT}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
                g.append(f'<text class="tl-ref-label" x="{W - RIGHT - 4}" y="{yy - 5:.1f}" text-anchor="end">'
                         f'SLA {target:g}%</text>')
        coords = [(x(p["t"]), y(p[key])) for p in pts]
        if len(coords) == 1:
            g.append(f'<circle class="tl-dot" cx="{coords[0][0]:.1f}" cy="{coords[0][1]:.1f}" r="4"/>')
        else:
            d = "M" + " L".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
            g.append(f'<path class="tl-line" d="{d}"/>')
        parts.append(f'<g class="tl-panel" data-key="{key}">{"".join(g)}</g>')

    # Band labels get their own row above the first panel: finding numbers at the start of their
    # window, "Steady state" at the end of its window
    last_end = 0.0
    for b in sorted(bands, key=lambda b: b["x"]):
        if b["steady"]:
            parts.append(f'<text class="tl-band-label" x="{b["x"] + b["w"] - 3:.1f}" y="{TOP - 32}" '
                         f'text-anchor="end">{escape(b["label"])}</text>')
            continue
        lx = max(b["x"] + 2, last_end + 4)
        last_end = lx + 7.5 * len(b["label"])
        parts.append(f'<text class="tl-band-label" x="{lx:.1f}" y="{TOP - 32}">{escape(b["label"])}</text>')

    # Time axis
    span_min = (x1 - x0) / 60
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 240) if span_min / s <= 8), 480)
    axis_y = TOP + len(PANELS) * PANEL_H + (len(PANELS) - 1) * GAP
    t = math.ceil(x0 / (step * 60)) * step * 60
    while t <= x1:
        parts.append(f'<text class="tl-tick" x="{x(t):.1f}" y="{axis_y + 18}" text-anchor="middle">'
                     f'{_minutes(t, step)}</text>')
        t += step * 60

    parts.append(f'<line class="tl-cross" x1="0" x2="0" y1="{TOP}" y2="{axis_y}" visibility="hidden"/>')
    parts.append(f'<rect class="tl-hit" x="{LEFT}" y="{TOP}" width="{plot_w}" height="{axis_y - TOP}" tabindex="0" '
                 f'aria-label="Timeline values: use the left and right arrow keys"/>')

    data = json.dumps({"x0": x0, "x1": x1, "left": LEFT, "width": plot_w, "w": W, "points": pts},
                      separators=(",", ":")).replace("</", "<\\/")
    return (
        f'<div class="tl-wrap" id="{uid}">'
        f'<svg class="tl" viewBox="0 0 {W} {height}" role="img" aria-label="Active users, throughput, p95 response '
        f'time and error rate over the run, with finding windows shaded">{"".join(parts)}</svg>'
        f'<div class="tl-tip" role="status" hidden></div>'
        f'<script type="application/json" class="tl-data">{data}</script>'
        f'</div>'
    )


def _error_target(doc: dict[str, Any]) -> float | None:
    for c in doc.get("sla", {}).get("checks", []) or []:
        if c.get("kind") == "error_rate":
            return float(c["target"])
    return None


def _bands(doc: dict[str, Any], x, x0: float, x1: float) -> list[dict[str, Any]]:
    out = []
    steady = doc.get("steady") or {}
    if steady.get("start_offset_s") is not None and steady.get("source") != "full":
        a, b = max(x0, steady["start_offset_s"]), min(x1, steady["end_offset_s"])
        if b > a:
            out.append({"x": x(a), "w": x(b) - x(a), "cls": "tl-band-steady", "label": "Steady state", "steady": True})
    for i, f in enumerate(doc.get("findings", []), 1):
        if f["type"] in ("sla_breach", "regression") or f.get("start_offset_s") is None:
            continue    # these cover the whole steady window; their band would add nothing
        a = max(x0, f["start_offset_s"])
        end = f.get("end_offset_s")
        b = x1 if end is None else min(x1, end)          # open-ended (saturation): from the knee onwards
        if f["type"] == "correlation" and (b - a) > 0.6 * (x1 - x0):
            continue    # a problem present for most of the run: a band over everything would hide the lines
        out.append({"x": x(a), "w": max(x(b) - x(a), 2.0), "cls": f"tl-band-{f['severity']}",
                    "label": f"#{i}", "steady": False})
    return out


def sparkline_svg(values: list[float], unit: str = "", width: int = 180, height: int = 40) -> str:
    """A small line for one measure over the run (per-label report); min, max and last value as text."""
    vals = [float(v or 0) for v in values]
    if not vals:
        return ""
    top = max(vals) or 1
    pad = 3
    xs = [pad + i * (width - 2 * pad) / max(len(vals) - 1, 1) for i in range(len(vals))]
    ys = [height - pad - (v / top) * (height - 2 * pad) for v in vals]
    shape = (f'<circle cx="{xs[0]:.1f}" cy="{ys[0]:.1f}" r="2.5" class="sp-line-dot"/>' if len(vals) == 1 else
             '<path class="sp-line" d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + '"/>')

    def fmt_v(v: float) -> str:
        return f"{v:,.0f}" if v >= 100 or unit == "ms" else f"{v:,.2f}".rstrip("0").rstrip(".")
    label = f"min {fmt_v(min(vals))}{unit}, max {fmt_v(max(vals))}{unit}, last {fmt_v(vals[-1])}{unit}"
    return (f'<svg class="sp" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
            f'aria-label="{escape(label)}"><title>{escape(label)}</title>'
            f'<line class="sp-base" x1="{pad}" x2="{width - pad}" y1="{height - pad}" y2="{height - pad}"/>{shape}</svg>')


# Hover layer: crosshair snapped to the nearest bucket, one tooltip with every panel's value.
SCRIPT = """
(function () {
  document.querySelectorAll('.tl-wrap').forEach(function (wrap) {
    var data = JSON.parse(wrap.querySelector('.tl-data').textContent);
    var svg = wrap.querySelector('svg'), hit = wrap.querySelector('.tl-hit');
    var cross = wrap.querySelector('.tl-cross'), tip = wrap.querySelector('.tl-tip');
    var pts = data.points, idx = -1;
    function sx(t) { return data.left + (t - data.x0) / ((data.x1 - data.x0) || 1) * data.width; }
    function fmt(v, d) { return Number(v).toLocaleString('en-US', {maximumFractionDigits: d, minimumFractionDigits: d}); }
    function off(s) { s = Math.max(0, Math.round(s)); var m = Math.floor(s / 60), r = s % 60;
      return 't+' + m + 'm' + (r ? (r < 10 ? '0' : '') + r + 's' : ''); }
    function row(value, label) {
      var div = document.createElement('div'), b = document.createElement('strong'), span = document.createElement('span');
      b.textContent = value; span.textContent = ' ' + label; div.appendChild(b); div.appendChild(span); return div;
    }
    function show(i) {
      if (i < 0 || i >= pts.length) return;
      idx = i; var p = pts[i], cx = sx(p.t);
      cross.setAttribute('x1', cx); cross.setAttribute('x2', cx); cross.setAttribute('visibility', 'visible');
      tip.replaceChildren();
      var head = document.createElement('div'); head.className = 'tl-tip-head';
      head.textContent = off(p.start) + (p.clock ? ' · ' + p.clock + ' UTC' : ''); tip.appendChild(head);
      tip.appendChild(row(fmt(p.users, 0), 'users'));
      tip.appendChild(row(fmt(p.tps, 1), 'requests/s'));
      tip.appendChild(row(fmt(p.p95, 0) + ' ms', 'p95'));
      tip.appendChild(row(fmt(p.err, 2) + '%', 'errors'));
      tip.hidden = false;
      var box = svg.getBoundingClientRect(), scale = box.width / data.w;
      var left = cx * scale + 12, tw = tip.offsetWidth;
      if (left + tw > box.width) left = cx * scale - tw - 12;
      tip.style.left = Math.max(0, left) + 'px'; tip.style.top = (24 * scale) + 'px';
    }
    function hide() { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; }
    function nearest(clientX) {
      var box = svg.getBoundingClientRect(), t = data.x0 + ((clientX - box.left) / box.width * data.w - data.left)
        / data.width * (data.x1 - data.x0), best = 0;
      for (var i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - t) < Math.abs(pts[best].t - t)) best = i;
      return best;
    }
    hit.addEventListener('pointermove', function (e) { show(nearest(e.clientX)); });
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('focus', function () { show(idx < 0 ? 0 : idx); });
    hit.addEventListener('blur', hide);
    hit.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { show(Math.min(pts.length - 1, idx + 1)); e.preventDefault(); }
      if (e.key === 'ArrowLeft') { show(Math.max(0, idx - 1)); e.preventDefault(); }
      if (e.key === 'Escape') hide();
    });
  });
})();
"""
