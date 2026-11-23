"""
Charts as PNG pictures, for documents that cannot take the HTML report's SVG (Word).

The same forms as the report: small multiples on one time axis, one y-axis per panel,
the steady state and finding windows shaded across all panels, a legend per panel when
it holds several lines. Light, print-friendly colours: the validated categorical steps
for a light surface, ink and hairlines from the report's light palette.
"""

from __future__ import annotations

import io
import math
from typing import Any

INK, INK_2, MUTED = (11, 11, 11), (82, 81, 78), (137, 135, 129)
GRID, AXIS, SURFACE = (225, 224, 217), (195, 194, 183), (255, 255, 255)
BAND = (236, 235, 231)
SEVERITY = {"critical": (248, 216, 216), "warning": (253, 236, 200), "info": (232, 232, 228)}
SERIES = [(42, 120, 214), (235, 104, 52), (27, 175, 122), (237, 161, 0), (232, 123, 164), (0, 131, 0),
          (98, 80, 214), (227, 73, 72)]
TEST_LINE = (60, 60, 58)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for path in (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
                 f"/usr/share/fonts/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
                 "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:                     # Pillow < 10.1
        return ImageFont.load_default()


def _nice(v: float, floor: float) -> float:
    x = max(v, floor)
    exp = 10 ** math.floor(math.log10(x))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * exp >= x * 1.04:
            return m * exp
    return 10 * exp


def _tick(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{v:.0f}" if v >= 10 or float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")


def _minutes(t: float, step_min: int) -> str:
    m = round(t / 60)
    return f"{m // 60}h{m % 60:02d}" if step_min >= 60 and m % 60 else f"{m // 60}h" if step_min >= 60 else f"{m}m"


def panels_png(panels: list[dict[str, Any]], x0: float, x1: float, *, bands: list[dict[str, Any]] | None = None,
               width: int = 1800, panel_h: int = 190, scale: float = 2.0, legend_below: bool = False) -> bytes:
    """A PNG of small multiples on one time axis.

    panels: [{"title", "lines": [{"name", "colour": (r, g, b), "points": [(seconds, value), …]}], "floor"}]
    bands:  [{"from", "to", "kind": "steady" | "critical" | "warning" | "info", "label"}] shaded in every panel
    """
    from PIL import Image, ImageDraw
    s = scale
    left, right, top_pad, gap, axis_h = int(70 * s), int(18 * s), int(30 * s), int(56 * s), int(34 * s)
    f_title, f_tick, f_legend = _font(int(13 * s), True), _font(int(11 * s)), _font(int(11 * s))
    plot_w = width - left - right
    # Legend under the time axis, in rows (several lines with long names would run into the title)
    legend_rows: list[list[dict[str, Any]]] = []
    if legend_below:
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        row: list[dict[str, Any]] = []
        used = 0.0
        for ln in [ln for pn in panels for ln in pn["lines"] if ln.get("name")]:
            w_item = probe.textlength(ln["name"], font=f_legend) + 40 * s
            if row and used + w_item > plot_w:
                legend_rows.append(row); row, used = [], 0.0
            row.append(ln); used += w_item
        if row:
            legend_rows.append(row)
    legend_h = int(len(legend_rows) * 22 * s + (10 * s if legend_rows else 0))
    height = top_pad + len(panels) * panel_h + (len(panels) - 1) * gap + axis_h + legend_h
    img = Image.new("RGB", (width, height), SURFACE)
    d = ImageDraw.Draw(img)

    def x(t: float) -> float:
        return left + (min(max(t, x0), x1) - x0) / ((x1 - x0) or 1) * plot_w

    for k, panel in enumerate(panels):
        y0 = top_pad + k * (panel_h + gap)
        values = [v for ln in panel["lines"] for _, v in ln["points"] if v is not None]
        top = _nice(max(values, default=0), panel.get("floor", 1))

        def y(v: float) -> float:
            return y0 + panel_h - (max(v, 0) / top) * panel_h

        d.text((left, y0 - int(22 * s)), panel["title"], font=f_title, fill=INK_2)
        named = [] if legend_below else [ln for ln in panel["lines"] if ln.get("name")]
        lx = width - right
        for ln in reversed(named):
            label = ln["name"] if len(ln["name"]) <= 30 else ln["name"][:29] + "…"
            tw = d.textlength(label, font=f_legend)
            lx -= tw + int(24 * s)
            ly = y0 - int(14 * s)
            d.line([(lx, ly), (lx + int(16 * s), ly)], fill=ln["colour"], width=int(2.5 * s))
            d.text((lx + int(20 * s), ly - int(7 * s)), label, font=f_legend, fill=INK_2)
            lx -= int(10 * s)
        for b in bands or []:
            if b["to"] > b["from"]:
                d.rectangle([x(b["from"]), y0, x(b["to"]), y0 + panel_h],
                            fill=BAND if b["kind"] == "steady" else SEVERITY.get(b["kind"], BAND))
        for frac in (0, 0.5, 1):
            yy = y(top * frac)
            d.line([(left, yy), (width - right, yy)], fill=AXIS if frac == 0 else GRID, width=max(1, int(s)))
            label = _tick(top * frac)
            d.text((left - int(8 * s) - d.textlength(label, font=f_tick), yy - int(7 * s)), label, font=f_tick, fill=MUTED)
        for ln in panel["lines"]:
            coords = [(x(t), y(v)) for t, v in ln["points"] if v is not None]
            if len(coords) == 1:
                cx, cy = coords[0]
                r = 3 * s
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ln["colour"])
            elif coords:
                d.line(coords, fill=ln["colour"], width=int(2 * s), joint="curve")

    axis_y = top_pad + len(panels) * panel_h + (len(panels) - 1) * gap
    span_min = (x1 - x0) / 60
    step = next((m for m in (1, 2, 5, 10, 15, 30, 60, 120, 240) if span_min / m <= 10), 480)
    t = math.ceil(x0 / (step * 60)) * step * 60
    while t <= x1:
        label = _minutes(t, step)
        d.text((x(t) - d.textlength(label, font=f_tick) / 2, axis_y + int(8 * s)), label, font=f_tick, fill=MUTED)
        t += step * 60
    ly = axis_y + axis_h + int(6 * s)
    for row in legend_rows:
        lx = left
        for ln in row:
            d.line([(lx, ly + 8 * s), (lx + 16 * s, ly + 8 * s)], fill=ln["colour"], width=int(2.5 * s))
            d.text((lx + 20 * s, ly + 1 * s), ln["name"], font=f_legend, fill=INK_2)
            lx += d.textlength(ln["name"], font=f_legend) + 40 * s
        ly += int(22 * s)
    for b in bands or []:                  # labels above the first panel
        if b.get("label") and b["to"] > b["from"]:
            label = b["label"]
            bx = x(b["to"]) - d.textlength(label, font=f_tick) - 4 if b["kind"] == "steady" else x(b["from"]) + 4
            d.text((bx, int(4 * s)), label, font=f_tick, fill=INK_2)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def timeline_png(doc: dict[str, Any]) -> bytes | None:
    """The test's timeline: active users, throughput, p95 and error rate, with the steady state and findings shaded."""
    from projectstack.results.chart import series
    pts = series(doc)
    if not pts:
        return None
    bucket = doc.get("timeline", {}).get("bucket_s", 60)
    x0 = min(0.0, pts[0]["start"])
    x1 = max(float(doc.get("run", {}).get("duration_s") or 0), pts[-1]["start"] + bucket)

    def line(key: str) -> list[dict[str, Any]]:
        return [{"name": "", "colour": SERIES[0], "points": [(p["t"], p[key]) for p in pts]}]
    panels = [{"title": "Active users", "lines": line("users"), "floor": 1},
              {"title": "Throughput (requests/s)", "lines": line("tps"), "floor": 1},
              {"title": "p95 response time (ms)", "lines": line("p95"), "floor": 10},
              {"title": "Error rate (%)", "lines": line("err"), "floor": 1}]
    return panels_png(panels, x0, x1, bands=_bands(doc))


def _bands(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    steady = doc.get("steady") or {}
    if steady.get("source") != "full" and steady.get("start_offset_s") is not None:
        out.append({"from": steady["start_offset_s"], "to": steady["end_offset_s"], "kind": "steady",
                    "label": "Steady state"})
    for i, f in enumerate(doc.get("findings") or [], 1):
        a, b = f.get("start_offset_s"), f.get("end_offset_s")
        if a is not None and f.get("type") not in ("sla_breach", "regression"):
            out.append({"from": a, "to": b if b is not None else a + 30, "kind": f.get("severity", "info"),
                        "label": f"#{i}"})
    return out


def relation_png(doc: dict[str, Any], rel: dict[str, Any] | None) -> bytes | None:
    """The test's users and p95 with the server lines under them (one colour per server in every panel)."""
    import re
    from projectstack.results.chart import series
    lines = (rel or {}).get("lines") or []
    pts = series(doc)
    if not lines or not pts:
        return None
    bucket = doc.get("timeline", {}).get("bucket_s", 60)
    x0 = min(0.0, pts[0]["start"])
    x1 = max(float(doc.get("run", {}).get("duration_s") or 0), pts[-1]["start"] + bucket)
    panels: list[dict[str, Any]] = [
        {"title": "Active users (test)", "floor": 1,
         "lines": [{"name": "", "colour": TEST_LINE, "points": [(p["t"], p["users"]) for p in pts]}]},
        {"title": "p95 response time (test, ms)", "floor": 10,
         "lines": [{"name": "", "colour": TEST_LINE, "points": [(p["t"], p["p95"]) for p in pts]}]},
    ]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    slots: dict[str, int] = {}
    for line in lines:
        metric, sep, resource = line["name"].partition(" · ")
        resource = resource if sep else line["name"]
        key = re.sub(r"\s*\([^)]*\)\s*$", "", resource).strip().lower()
        k = slots.setdefault(key, len(slots))
        g = groups.setdefault((metric, line["unit"]), {
            "title": f"{metric} ({line['unit']})" if line["unit"] else metric,
            "floor": 10 if line["unit"] == "%" else 1, "lines": []})
        g["lines"].append({"name": resource, "colour": SERIES[k % len(SERIES)], "points": [tuple(p) for p in line["points"]]})
    panels += list(groups.values())
    steady = doc.get("steady") or {}
    bands = ([{"from": steady["start_offset_s"], "to": steady["end_offset_s"], "kind": "steady", "label": "Steady state"}]
             if steady.get("start_offset_s") is not None else [])
    return panels_png(panels, x0, x1, bands=bands, panel_h=150)
