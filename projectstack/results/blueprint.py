"""
"Create a report": a new performance report in the exact format of a past one.

The blueprint is a finished report (.docx) from an earlier test cycle. The new report is a copy of
it, so its cover, styles, fonts, numbering, header, footer and logos are the blueprint's own, with
every test-specific part rebuilt from analysed runs:

  kept               cover page, attention notice, document authorisation, sections before
                     "Performance Highlights & Observations" (introduction, objective, scope,
                     environment …), and their tables — project text that changes rarely
  updated            the month on the cover, the date in the version table, the workload table's
                     users per journey (from the runs' thread groups), the table of contents (Word
                     refreshes it when the file is opened)
  rebuilt per run    one copy of the blueprint's first "4.x" subsection per run, with its heading,
                     bullets (when and how the test ran, the statistics, the observations, the server
                     observations), the Performance Metrics table (JMeter's Aggregate Report columns,
                     over the whole run), the Running Vusers, Hits Per Second, Throughput and Response
                     Time graphs, and the Server Metrics screenshots with what the AI read from them
  drafted            "Tuning Recommendations" and "Conclusion", from the runs' findings and figures —
                     a draft to review

Paragraphs are cloned from the blueprint's own and only their text is replaced, so bullets, levels,
fonts and the bold figures look the same.
"""

from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from projectstack.results import fmt

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_MONTH_RE = re.compile(rf"^({'|'.join(_MONTHS)})\s+\d{{4}}$")
# Figures worth bold, as in the blueprint: dates and windows, numbers with units, plain numbers
_BOLD_RE = re.compile(
    rf"(?:{'|'.join(_MONTHS)}) \d{{1,2}}, \d{{4}}(?:, [0-9:]+ ?(?:AM|PM)? ?[-–] ?[0-9:]+ ?(?:AM|PM)? ?UTC)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:ms|s|%|req/sec|hit/sec|hits/sec|users|leads|minutes?|hours?)\b"
    r"|\b\d[\d,]*(?:\.\d+)?\b")
GRAPHS = {"running vusers": "users", "hits per second": "hits", "throughput": "bytes", "response time": "response"}
_CAPTION = {
    "users": "Active virtual users per journey (thread group) over the run.",
    "hits": "Requests sent to the server per second over the run.",
    "bytes": "Bytes received from and sent to the server per second over the run.",
    "response": "Average response time of each transaction per minute over the run.",
}


class BlueprintError(RuntimeError):
    """The blueprint is not a report this can follow, explained for the user."""


@dataclass
class RunInput:
    """One analysed run and the title of its subsection ("Incremental Load Test - US region")."""
    folder: Path
    doc: dict[str, Any]
    title: str = ""
    region_label: str = ""          # the workload table's column for this run ("US")


@dataclass
class Plan:
    """Where the blueprint's parts are (indexes into the body's children)."""
    section4: int
    subsections: list[int]
    after4: int                     # the next top-level heading (recommendations), or the end
    recommendations: int | None
    conclusion: int | None
    month: int | None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading the blueprint
# ---------------------------------------------------------------------------

def _qn(tag: str) -> str:
    from docx.oxml.ns import qn
    return qn(tag)


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(_qn("w:t")))


def _style(document, el) -> str:
    ppr = el.find(_qn("w:pPr"))
    sid = ppr.find(_qn("w:pStyle")) if ppr is not None else None
    if sid is None:
        return ""
    try:
        return document.styles.get_by_id(sid.get(_qn("w:val")), 1).name or ""
    except Exception:  # noqa: BLE001
        return sid.get(_qn("w:val")) or ""


def _level(document, el) -> int | None:
    """The heading level of a paragraph (Heading 3 → 3), or None."""
    m = re.match(r"heading\s*(\d)", _style(document, el), re.I)
    return int(m.group(1)) if m else None


def _list_level(el, document=None) -> int | None:
    """0 for a first-level bullet, 1 for a nested one; None for a paragraph that is not in a list.

    Google Docs numbers each paragraph; Word usually uses list styles ("List Bullet 2"), so both count.
    """
    ppr = el.find(_qn("w:pPr"))
    num = ppr.find(_qn("w:numPr")) if ppr is not None else None
    if num is not None:
        ilvl = num.find(_qn("w:ilvl"))
        return int(ilvl.get(_qn("w:val"))) if ilvl is not None else 0
    if document is not None:
        m = re.match(r"list (?:bullet|number|paragraph)(?: (\d))?$", _style(document, el), re.I)
        if m:
            return int(m.group(1)) - 1 if m.group(1) else 0
    return None


def plan(document) -> Plan:
    body = list(document.element.body)
    heads = [(i, _level(document, el), _text(el).strip()) for i, el in enumerate(body)
             if el.tag == _qn("w:p") and _level(document, el)]
    s4 = next(((i, lv) for i, lv, t in heads if "performance highlights" in t.lower()), None)
    if s4 is None:
        raise BlueprintError('The blueprint has no "Performance Highlights & Observations" heading to rebuild')
    i4, lv4 = s4
    after = next((i for i, lv, _ in heads if i > i4 and lv <= lv4), len(body) - 1)
    subs = [i for i, lv, _ in heads if i4 < i < after and lv == lv4 + 1]
    if not subs:
        raise BlueprintError("The blueprint's highlights section has no per-test subsection (4.1 …) to copy")
    rec = next((i for i, lv, t in heads if i >= after and lv == lv4 and "recommend" in t.lower()), None)
    con = next((i for i, lv, t in heads if i >= after and lv == lv4 and "conclusion" in t.lower()), None)
    month = next((i for i, el in enumerate(body[:i4]) if el.tag == _qn("w:p") and _MONTH_RE.match(_text(el).strip())), None)
    return Plan(i4, subs, after, rec, con, month)


# ---------------------------------------------------------------------------
# Editing cloned paragraphs
# ---------------------------------------------------------------------------

def set_text(p, text: str, *, bold_figures: bool = False, size_half_points: int | None = None) -> None:
    """Replace a paragraph's text, keeping its paragraph properties and its first run's look.

    bold_figures: dates, windows and numbers in bold, as the blueprint writes them.
    """
    from docx.oxml import OxmlElement
    first = next(p.iter(_qn("w:r")), None)
    rpr = copy.deepcopy(first.find(_qn("w:rPr"))) if first is not None and first.find(_qn("w:rPr")) is not None else None
    if rpr is not None:
        for tag in ("w:b", "w:bCs"):
            for el in rpr.findall(_qn(tag)):
                rpr.remove(el)
    for child in list(p):
        if child.tag != _qn("w:pPr"):
            p.remove(child)
    pieces: list[tuple[str, bool]] = []
    pos = 0
    for m in (_BOLD_RE.finditer(text) if bold_figures else ()):
        if m.start() > pos:
            pieces.append((text[pos:m.start()], False))
        pieces.append((m.group(0), True))
        pos = m.end()
    pieces.append((text[pos:], False))
    for piece, bold in pieces:
        if not piece:
            continue
        r = OxmlElement("w:r")
        props = copy.deepcopy(rpr) if rpr is not None else OxmlElement("w:rPr")
        if bold:
            props.insert(0, OxmlElement("w:b"))
        if size_half_points:
            for el in props.findall(_qn("w:sz")) + props.findall(_qn("w:szCs")):
                props.remove(el)
            sz = OxmlElement("w:sz")
            sz.set(_qn("w:val"), str(size_half_points))
            props.append(sz)
        if len(props):
            r.append(props)
        t = OxmlElement("w:t")
        t.text = piece
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t)
        p.append(r)


def _replace_picture(document, p, png: bytes, counter: list[int]) -> None:
    """Point a cloned picture paragraph at a new picture, keeping its width and the new aspect ratio."""
    from PIL import Image
    blip = p.find(".//" + _qn("a:blip"))
    if blip is None:
        return
    rid, _ = document.part.get_or_add_image(io.BytesIO(png))
    blip.set(_qn("r:embed"), rid)
    w, h = Image.open(io.BytesIO(png)).size
    extent = p.find(".//" + _qn("wp:extent"))
    if extent is not None:
        cx = int(extent.get("cx"))
        cy = int(cx * h / max(w, 1))
        extent.set("cy", str(cy))
        for ext in p.iter(_qn("a:ext")):
            if ext.get("cx") is not None:
                ext.set("cx", str(cx)); ext.set("cy", str(cy))
    for doc_pr in p.iter(_qn("wp:docPr")):     # unique ids for every picture
        counter[0] += 1
        doc_pr.set("id", str(counter[0]))


def _styled_table(proto_tbl, headers: list[str], rows: list[list[str]], ratios: list[float],
                  right: set[int], size_half_points: int = 14):
    """A table in the blueprint's own table look (borders, header shading, fonts), with new columns."""
    from docx.oxml import OxmlElement
    tbl = copy.deepcopy(proto_tbl)
    trs = tbl.findall(_qn("w:tr"))
    head_tr, body_tr = trs[0], trs[1] if len(trs) > 1 else trs[0]
    for tr in trs:
        tbl.remove(tr)
    grid = tbl.find(_qn("w:tblGrid"))
    total = int(sum(float(g.get(_qn("w:w")) or 0) for g in grid.findall(_qn("w:gridCol")))) or 9000   # Google Docs writes decimals
    for g in list(grid):
        grid.remove(g)
    widths = [int(total * r / sum(ratios)) for r in ratios]
    for wdt in widths:
        g = OxmlElement("w:gridCol")
        g.set(_qn("w:w"), str(wdt))
        grid.append(g)

    def row(proto_tr, values: list[str], header: bool):
        tr = copy.deepcopy(proto_tr)
        tcs = tr.findall(_qn("w:tc"))
        proto_tc = tcs[0]
        for tc in tcs:
            tr.remove(tc)
        for k, (value, wdt) in enumerate(zip(values, widths)):
            tc = copy.deepcopy(proto_tc)
            tc_pr = tc.find(_qn("w:tcPr"))
            if tc_pr is not None:
                for tag in ("w:gridSpan", "w:vMerge", "w:hMerge"):
                    for el in tc_pr.findall(_qn(tag)):
                        tc_pr.remove(el)
                tc_w = tc_pr.find(_qn("w:tcW"))
                if tc_w is not None:
                    tc_w.set(_qn("w:w"), str(wdt)); tc_w.set(_qn("w:type"), "dxa")
            paras = tc.findall(_qn("w:p"))
            for extra in paras[1:]:
                tc.remove(extra)
            p = paras[0]
            set_text(p, value, size_half_points=size_half_points)
            if k in right and not header:
                ppr = p.find(_qn("w:pPr"))
                if ppr is None:
                    ppr = OxmlElement("w:pPr"); p.insert(0, ppr)
                for el in ppr.findall(_qn("w:jc")):
                    ppr.remove(el)
                jc = OxmlElement("w:jc"); jc.set(_qn("w:val"), "right")
                ppr.insert_element_before(jc, "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
                                          "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
            tr.append(tc)
        return tr

    tbl.append(row(head_tr, headers, True))
    for values in rows:
        tbl.append(row(body_tr, values, False))
    return tbl


# ---------------------------------------------------------------------------
# Data from a run
# ---------------------------------------------------------------------------

def aggregate(db, run_id: str) -> list[dict[str, Any]]:
    """JMeter's Aggregate Report for every request over the whole run (exact percentiles)."""
    from projectstack.results.metrics import in_kind
    rows = db.dicts(
        f"""SELECT label, count() AS n, avg(elapsed) AS avg, quantileExact(0.5)(elapsed) AS med,
                   quantileExact(0.9)(elapsed) AS p90, quantileExact(0.95)(elapsed) AS p95,
                   quantileExact(0.99)(elapsed) AS p99, min(elapsed) AS mn, max(elapsed) AS mx,
                   countIf(NOT success) AS err, sum(bytes) AS rx, sum(sent_bytes) AS tx,
                   toUnixTimestamp64Milli(min(ts)) AS first_ms,
                   (toUnixTimestamp64Milli(max(ts)) - toUnixTimestamp64Milli(min(ts))) / 1000 AS span
            FROM {db.db}.samples WHERE run_id = {{run:String}} AND {in_kind(db, "request")}
            GROUP BY label ORDER BY first_ms""", {"run": run_id})
    out = []
    for r in rows:
        span = max(float(r["span"]), 1.0)
        out.append({"label": r["label"], "samples": int(r["n"]), "avg": float(r["avg"]), "median": int(r["med"]),
                    "p90": int(r["p90"]), "p95": int(r["p95"]), "p99": int(r["p99"]), "min": int(r["mn"]),
                    "max": int(r["mx"]), "error_pct": 100.0 * int(r["err"]) / max(int(r["n"]), 1),
                    "per_s": int(r["n"]) / span, "rx_kb_s": int(r["rx"]) / 1024 / span,
                    "tx_kb_s": int(r["tx"]) / 1024 / span})
    return out


def _journey(thread_group: str) -> str:
    """ "US - Search - 200 users" → "Search" (the region prefix and the user count dropped)."""
    name = re.sub(r"\s*-\s*\d+\s*users?\s*$", "", thread_group, flags=re.I)
    return re.sub(r"^[A-Z]{2,3}\s*-\s*", "", name).strip()


def graph_series(db, run_id: str, start_ms: int) -> dict[str, Any]:
    """Per-minute series for the four graphs: users per journey, hits/s, bytes/s, response time per transaction."""
    from projectstack.results.metrics import in_kind
    p = {"run": run_id}
    minute = "toUnixTimestamp(toStartOfMinute(ts)) * 1000"
    users: dict[str, list[tuple[float, float]]] = {}
    for t, grp, n in db.rows(
            f"""SELECT {minute} AS t, replaceRegexpOne(thread_name, ' \\\\d+-\\\\d+$', '') AS g, max(grp_threads)
                FROM {db.db}.samples WHERE run_id = {{run:String}} GROUP BY t, g ORDER BY t""", p):
        users.setdefault(g if (g := str(grp)) else "users", []).append(((t - start_ms) / 1000 + 30, float(n)))
    hits, rx, tx = [], [], []
    for t, n, b_in, b_out in db.rows(
            f"""SELECT {minute} AS t, count(), sum(bytes), sum(sent_bytes) FROM {db.db}.samples
                WHERE run_id = {{run:String}} AND {in_kind(db, "request")} GROUP BY t ORDER BY t""", p):
        x = (t - start_ms) / 1000 + 30
        hits.append((x, n / 60)); rx.append((x, b_in / 60)); tx.append((x, b_out / 60))
    kind = "transaction" if db.rows(f"SELECT count() FROM {db.db}.run_labels FINAL WHERE run_id = {{run:String}} "
                                    "AND kind = 'transaction'", p)[0][0] else "request"
    response: dict[str, list[tuple[float, float]]] = {}
    for t, label, avg in db.rows(
            f"""SELECT {minute} AS t, label, avg(elapsed) FROM {db.db}.samples
                WHERE run_id = {{run:String}} AND {in_kind(db, kind)} GROUP BY t, label ORDER BY t""", p):
        response.setdefault(str(label), []).append(((t - start_ms) / 1000 + 30, float(avg)))
    if len(response) > 8:                      # the eight busiest, so the lines stay readable
        keep = sorted(response, key=lambda k: -len(response[k]))[:8]
        response = {k: response[k] for k in keep}
    return {"users": users, "hits": hits, "rx": rx, "tx": tx, "response": response, "kind": kind}


def graphs_png(doc: dict[str, Any], series: dict[str, Any]) -> dict[str, bytes]:
    from projectstack.results.chart_png import SERIES, panels_png
    duration = float(doc["run"].get("duration_s") or 0)
    steady = doc.get("steady") or {}
    bands = ([{"from": steady["start_offset_s"], "to": steady["end_offset_s"], "kind": "steady", "label": "Steady state"}]
             if steady.get("start_offset_s") is not None and steady.get("source") != "full" else [])

    def one(title: str, lines: list[dict[str, Any]], floor: float = 1) -> bytes:
        return panels_png([{"title": title, "lines": lines, "floor": floor}], 0, duration, bands=bands,
                          width=1800, panel_h=560, legend_below=True)
    users = [{"name": g if re.search(r"\busers?\b", g, re.I) else f"{g} - {int(max(v for _, v in pts))} users",
              "colour": SERIES[k % len(SERIES)], "points": pts}
             for k, (g, pts) in enumerate(sorted(series["users"].items()))]
    return {
        "users": one("Active virtual users per journey", users),
        "hits": one("Hits per second", [{"name": "", "colour": SERIES[0], "points": series["hits"]}]),
        "bytes": one("Bytes throughput per second", [
            {"name": "Bytes received per second", "colour": SERIES[3], "points": series["rx"]},
            {"name": "Bytes sent per second", "colour": SERIES[0], "points": series["tx"]}]),
        "response": one(f"Average response time per {series['kind']} (ms)", [
            {"name": k, "colour": SERIES[i % len(SERIES)], "points": pts}
            for i, (k, pts) in enumerate(series["response"].items())], floor=10),
    }


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _ordinal(day: int) -> str:
    return f"{day}{'th' if 10 <= day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')}"


def _region_label(ctx: dict[str, Any]) -> str:
    region = (ctx.get("region") or "").strip()
    m = re.match(r"([a-z]{2})-", region)
    return m.group(1).upper() if m else region


def default_title(doc: dict[str, Any], ctx: dict[str, Any], facts: dict[str, Any]) -> str:
    """ "Incremental Load Test - US region", or the scenario when the run has no region."""
    import string
    kind = string.capwords((facts.get("test_type") or "load test").strip())
    label = _region_label(ctx)
    return f"{kind} - {label} region" if label else f"{kind} - {doc['run'].get('scenario', '')}"


def thread_groups(db, run_id: str) -> dict[str, int]:
    """Peak active users per journey (thread group) in a run: {"Search": 200, …}."""
    rows = db.rows(f"""SELECT replaceRegexpOne(thread_name, ' \\d+-\\d+$', '') AS g, max(grp_threads)
                       FROM {db.db}.samples WHERE run_id = {{run:String}} GROUP BY g""", {"run": run_id})
    out: dict[str, int] = {}
    for g, n in rows:
        name = _journey(str(g))
        out[name] = max(out.get(name, 0), int(n))
    return out


def build(blueprint: bytes, runs: list[RunInput], *, settings: dict[str, str], db=None,
          use_llm: bool = False, provider_spec: str | None = None, report_date: datetime | None = None,
          progress=lambda _: None) -> tuple[bytes, list[str]]:
    """(.docx bytes, notes): the blueprint rebuilt for these runs."""
    from docx import Document
    from projectstack.results import highlights as hl, screenshots
    if not runs:
        raise BlueprintError("Choose at least one analysed run")
    document = Document(io.BytesIO(blueprint))
    pl = plan(document)
    body = document.element.body
    els = list(body)
    notes: list[str] = []
    now = report_date or datetime.now()
    pic_ids = [max([int(d.get("id") or 0) for d in body.iter(_qn("wp:docPr"))] or [0]) + 1000]

    # Prototype of one run's subsection: from its heading to the next subsection (or section 5)
    first, nxt = pl.subsections[0], (pl.subsections[1] if len(pl.subsections) > 1 else pl.after4)
    proto = els[first:nxt]
    proto_heading = proto[0]
    bullets = [el for el in proto if el.tag == _qn("w:p") and _list_level(el, document) is not None]
    proto_l0 = next((el for el in bullets if _list_level(el, document) == 0), None)
    proto_l1 = next((el for el in bullets if _list_level(el, document) == 1), proto_l0)
    blocks = _blocks(document, proto)
    proto_table = next((el for el in body if el.tag == _qn("w:tbl")), None)
    section_no = re.match(r"\s*(\d+)", _text(els[pl.section4]))
    n4 = section_no.group(1) if section_no else "4"

    # Remove the blueprint's own subsections; the new ones go where they were
    anchor = els[pl.after4] if pl.after4 < len(els) else None
    for el in els[first:pl.after4]:
        body.remove(el)

    run_data = []
    for k, r in enumerate(runs, 1):
        progress(f"Writing section {n4}.{k} from run {r.doc['run']['run_id']} …")
        ctx = hl.resolve_context(r.folder, r.doc["run"].get("scenario", ""), Path(settings["runs_dir"]))
        section, _, warnings = hl.get(r.doc, r.folder, ctx, use_llm=use_llm, provider_spec=provider_spec,
                                      settings=settings)
        notes += warnings
        facts = hl.facts(r.doc, ctx)
        shots = screenshots.report_section(r.folder, r.doc, ctx, embed=False)
        title = r.title or default_title(r.doc, ctx, facts)
        new = _subsection(document, r, k, n4, title, section.content, shots, proto_heading, proto_l0, proto_l1,
                          blocks, proto_table, db, pic_ids, notes)
        for el in new:
            if anchor is not None:
                anchor.addprevious(el)
            else:
                body.append(el)
        run_data.append({"run": r, "ctx": ctx, "facts": facts, "title": title, "h": section.content,
                         "label": r.region_label or _region_label(ctx) or title,
                         "groups": thread_groups(db, r.doc["run"]["run_id"]) if db is not None else {}})

    _recommendations(document, pl, run_data, notes)
    _conclusion(document, pl, run_data)
    _workload(document, run_data, notes)
    els = list(body)
    if pl.month is not None:
        set_text(els[pl.month], f"{_MONTHS[now.month - 1]} {now.year}")
    _version_date(document, now)
    _refresh_fields_on_open(document)
    _drop_unused_pictures(document)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue(), notes


def _blocks(document, proto: list) -> dict[str, list]:
    """The subsection's blocks under its smaller headings: {"metrics": [...], "users": [...], …}."""
    blocks: dict[str, list] = {}
    current = None
    for el in proto[1:]:
        lv = _level(document, el) if el.tag == _qn("w:p") else None
        if lv is not None:
            t = _text(el).strip().lower()
            current = ("metrics" if "metrics" in t and "server" not in t else "server" if "server" in t
                       else next((v for k, v in GRAPHS.items() if k in t), t))
            blocks[current] = [el]
        elif current is not None:
            blocks[current].append(el)
    return blocks


def _clone(el):
    return copy.deepcopy(el)


def _subsection(document, r: RunInput, k: int, n4: str, title: str, h: dict[str, Any], shots: dict | None,
                proto_heading, proto_l0, proto_l1, blocks: dict[str, list], proto_table, db, pic_ids, notes):
    out = []
    heading = _clone(proto_heading)
    set_text(heading, f"{n4}.{k} {title}")
    out.append(heading)

    def bullet(text: str, level: int = 0):
        p = _clone(proto_l0 if level == 0 or proto_l1 is None else proto_l1)
        set_text(p, text, bold_figures=True)
        out.append(p)
    for s in h["intro"]:
        bullet(s)
    bullet("Below are the high-level statistics from the test:")
    for s in h["stats"]:
        bullet(f"{s['name']}: {s['value']}", 1)
    observations = list(h["observations"])
    server = (shots or {}).get("observations") if shots and shots.get("analysed") else []
    for o in observations[:2] + list(server or []) + observations[2:]:
        bullet(o)

    # Performance Metrics: the Aggregate Report, inline
    block = blocks.get("metrics")
    if block:
        out.append(_clone(block[0]))
        intro = next((el for el in block[1:] if el.tag == _qn("w:p") and _text(el).strip()
                      and el.find(".//" + _qn("w:hyperlink")) is None), None)
        if intro is not None:
            p = _clone(intro)
            where = title.split(" - ")[-1] if " - " in title else title
            set_text(p, "The table below gives the detailed response times of all the individual requests that were "
                        f"executed as part of the load test execution in the {where}.")
            out.append(p)
        if db is not None and proto_table is not None:
            rows = [[a["label"], fmt.num(a["samples"]), fmt.num(a["avg"]), fmt.num(a["median"]), fmt.num(a["p90"]),
                     fmt.num(a["p95"]), fmt.num(a["p99"]), fmt.num(a["min"]), fmt.num(a["max"]),
                     f"{a['error_pct']:.2f}%", f"{a['per_s']:.2f}", f"{a['rx_kb_s']:.2f}", f"{a['tx_kb_s']:.2f}"]
                    for a in aggregate(db, r.doc["run"]["run_id"])]
            # JMeter's Aggregate Report columns, with headers short enough for A4 portrait (times in ms)
            out.append(_styled_table(proto_table, ["Label", "Samples", "Avg", "Median", "90%", "95%", "99%", "Min",
                                                   "Max", "Error %", "Hits/s", "KB/s in", "KB/s out"], rows,
                                     [4.0, 1.25, 1.0, 1.3, 0.95, 0.95, 0.95, 0.85, 1.1, 1.05, 1.05, 1.1, 1.15],
                                     set(range(1, 13)), size_half_points=13))
            p = _clone(intro) if intro is not None else None
            if p is not None:
                set_text(p, "Times are in milliseconds over the whole run, as in JMeter's Aggregate Report.",
                         size_half_points=16)
                out.append(p)
        else:
            notes.append("The Aggregate Report table needs the results database; it was left out")

    # The four graphs, redrawn from the run's samples
    pngs = {}
    if db is not None:
        start_ms = int(datetime.fromisoformat(r.doc["run"]["start"]).timestamp() * 1000)
        pngs = graphs_png(r.doc, graph_series(db, r.doc["run"]["run_id"], start_ms))
    for key in ("users", "hits", "bytes", "response"):
        block = blocks.get(key)
        if not block:
            continue
        for el in block:
            c = _clone(el)
            if c.find(".//" + _qn("a:blip")) is not None:
                if key in pngs:
                    _replace_picture(document, c, pngs[key], pic_ids)
                else:
                    continue
            out.append(c)

    # Server metrics: the run's screenshots, captioned, with what the AI read from each
    block = blocks.get("server")
    if block:
        out.append(_clone(block[0]))
        caption = next((el for el in block[1:] if el.tag == _qn("w:p") and _text(el).strip()
                        and el.find(".//" + _qn("a:blip")) is None and _is_bold(el)), None)
        picture = next((el for el in block[1:] if el.find(".//" + _qn("a:blip")) is not None), None)
        describe = next((el for el in block[1:] if el.tag == _qn("w:p") and _text(el).strip()
                         and not _is_bold(el) and el.find(".//" + _qn("a:blip")) is None), None)
        pics = (shots or {}).get("pictures") or []
        if not pics and describe is not None:
            p = _clone(describe)
            set_text(p, "Attach this run's server metrics screenshots in the Result Analyser to fill this section.")
            _highlight(p)
            out.append(p)
            notes.append(f"{title}: no server metrics screenshots attached")
        for pic in pics:
            if caption is not None:
                c = _clone(caption); set_text(c, pic.get("caption") or pic.get("heading") or pic["name"]); out.append(c)
            path = r.folder / pic["link"]
            if picture is not None and path.exists():
                from projectstack.results.screenshots import shrink
                data, _ = shrink(path.read_bytes(), "image/png", 1600, 3 * 1024 * 1024)
                c = _clone(picture); _replace_picture(document, c, data, pic_ids); out.append(c)
            if describe is not None:
                text = " ".join(pic.get("observations") or []) or pic.get("heading") or ""
                if text:
                    c = _clone(describe); set_text(c, text); out.append(c)
    return out


def _is_bold(p) -> bool:
    r = next(p.iter(_qn("w:r")), None)
    return r is not None and r.find(".//" + _qn("w:b")) is not None


def _highlight(p) -> None:
    from docx.oxml import OxmlElement
    for r in p.iter(_qn("w:r")):
        rpr = r.find(_qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr"); r.insert(0, rpr)
        hl = OxmlElement("w:highlight"); hl.set(_qn("w:val"), "yellow")
        rpr.append(hl)


def _section_body(document, heading_index: int | None) -> tuple[Any, list]:
    """A top-level section's heading element and the paragraphs/tables up to the next heading of its level."""
    if heading_index is None:
        return None, []
    els = list(document.element.body)
    head = els[heading_index]
    level = _level(document, head)
    out = []
    for el in els[heading_index + 1:]:
        if el.tag == _qn("w:sectPr"):
            break
        if el.tag == _qn("w:p") and (lv := _level(document, el)) is not None and lv <= level:
            break
        out.append(el)
    return head, out


def _recommendations(document, pl: Plan, runs: list[dict[str, Any]], notes: list[str]) -> None:
    """A draft of "Tuning Recommendations" from the runs: the slowest journey, findings, server links."""
    els = list(document.element.body)
    idx = next((i for i, el in enumerate(els) if el.tag == _qn("w:p") and _level(document, el)
                and "recommend" in _text(el).lower()), None)
    head, content = _section_body(document, idx)
    if head is None:
        return
    l0 = next((el for el in content if el.tag == _qn("w:p") and _list_level(el, document) == 0), None)
    l1 = next((el for el in content if el.tag == _qn("w:p") and _list_level(el, document) == 1), l0)
    if l0 is None:
        return
    items: list[tuple[str, list[str]]] = []
    slow = [(d["facts"].get("slowest_endpoint"), d) for d in runs if d["facts"].get("slowest_endpoint")]
    if slow:
        name = slow[0][0].get("name") or slow[0][0].get("shown")
        per = ", ".join(f"{fmt.ms(s.get('p50_ms'))} median ({fmt.ms(s.get('p95_ms'))} p95) in {d['label']}" for s, d in slow)
        items.append((f"Optimize the slowest endpoint: {name}.",
                      [f"{slow[0][0].get('shown') or name} registered the highest latency: {per}."]))
    for d in runs:
        for f in d["run"].doc.get("findings", []):
            if f.get("severity") in ("critical", "warning") and f.get("type") not in ("regression",):
                items.append((f"{d['label']}: {f['title']}.", [f.get("detail", "")]))
    if len(runs) > 1:
        p95s = [(d["facts"]["response_times"].get("p95_ms"), d["label"]) for d in runs
                if d["facts"].get("response_times", {}).get("p95_ms")]
        if len(p95s) > 1:
            hi, lo = max(p95s), min(p95s)
            if hi[0] and lo[0] and hi[0] > lo[0] * 1.25:
                items.append((f"Investigate the higher latency in {hi[1]}.",
                              [f"p95 response time was {fmt.ms(hi[0])} in {hi[1]} against {fmt.ms(lo[0])} in {lo[1]}."]))
    if not items:
        items.append(("No tuning is required for the load tested.",
                      ["Response times held steady and no detector raised a problem."]))
    anchor = head
    for el in content:
        el.getparent().remove(el)
    for title, details in items[:8]:
        p = _clone(l0); set_text(p, title); _bold_all(p); anchor.addnext(p); anchor = p
        for dtl in details:
            if dtl:
                q = _clone(l1); set_text(q, dtl, bold_figures=False); anchor.addnext(q); anchor = q
    notes.append("Tuning Recommendations is a draft from the findings: review it and add project-specific advice")


def _bold_all(p) -> None:
    from docx.oxml import OxmlElement
    for r in p.iter(_qn("w:r")):
        rpr = r.find(_qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr"); r.insert(0, rpr)
        if rpr.find(_qn("w:b")) is None:
            rpr.insert(0, OxmlElement("w:b"))


def _conclusion(document, pl: Plan, runs: list[dict[str, Any]]) -> None:
    els = list(document.element.body)
    idx = next((i for i, el in enumerate(els) if el.tag == _qn("w:p") and _level(document, el)
                and "conclusion" in _text(el).lower()), None)
    head, content = _section_body(document, idx)
    paras = [el for el in content if el.tag == _qn("w:p") and _text(el).strip()]
    if head is None or not paras:
        return
    labels = [d["label"] for d in runs]
    users = [fmt.num(d["run"].doc["run"].get("peak_users")) for d in runs]
    errors = sum(int((d["facts"].get("totals") or {}).get("errors") or 0) for d in runs)
    requests = sum(int((d["facts"].get("totals") or {}).get("requests") or 0) for d in runs)
    dates = sorted({d["facts"]["window"]["text"].rsplit(", ", 1)[0] for d in runs})
    kind = (runs[0]["facts"].get("test_type") or "load testing").replace(" test", " testing")
    many = len(runs) > 1
    first = (f"{kind[:1].upper()}{kind[1:]} across {_join(labels)} ({_join(users)} virtual users"
             f"{', respectively' if many else ''}) was completed on {_join(dates)}, "
             + (f"without any errors across {fmt.num(requests)} requests." if errors == 0 else
                f"with {fmt.num(errors)} errors across {fmt.num(requests)} requests "
                f"({100 * errors / max(requests, 1):.2f}%)."))
    verdicts = [d["run"].doc.get("verdict") for d in runs]
    second = ("Response times stayed within the SLA targets in every run." if all(v == "pass" for v in verdicts) else
              "The findings above call for attention before release; see Tuning Recommendations."
              if any(v in ("fail", "at_risk") for v in verdicts) else
              "No SLA targets were set, so the results are reported as measured; see the observations for each run.")
    set_text(paras[0], first, bold_figures=True)
    if len(paras) > 1:
        set_text(paras[1], second, bold_figures=True)
        for extra in paras[2:]:
            extra.getparent().remove(extra)


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + ", and " + items[-1] if items else ""


def _workload(document, runs: list[dict[str, Any]], notes: list[str]) -> None:
    """Fill the workload table's users per journey for the runs whose column it has (US, CA …)."""
    from docx.table import Table
    tbl = None
    els = list(document.element.body)
    for i, el in enumerate(els):
        if el.tag == _qn("w:p") and "workload distribution" in _text(el).lower() and _level(document, el):
            tbl = next((e for e in els[i + 1:i + 6] if e.tag == _qn("w:tbl")), None)
            break
    if tbl is None:
        return
    table = Table(tbl, document)
    header = [c.text.strip() for c in table.rows[min(1, len(table.rows) - 1)].cells]
    top = [c.text.strip() for c in table.rows[0].cells]
    filled = 0
    for d in runs:
        col = next((j for j, t in enumerate(top) if t and t.upper() == d["label"].upper()), None)
        if col is None:
            continue
        groups = d.get("groups") or {}
        if not groups:
            continue
        total = sum(groups.values())
        table.rows[1].cells[col].text = fmt.num(total)
        for row in table.rows[2:]:
            name = row.cells[1].text.strip().lower()
            users = next((u for g, u in groups.items() if g.lower() == name or g.lower() in name or name in g.lower()), None)
            if users is not None:
                row.cells[col].text = fmt.num(users)
                filled += 1
    if not filled:
        notes.append("The workload table was kept as it is (its region columns did not match the runs)")


def _version_date(document, now: datetime) -> None:
    for table in document.tables:
        head = [c.text.strip().lower() for c in table.rows[0].cells]
        if "version" in head and "date" in head and len(table.rows) > 1:
            col = head.index("date")
            cell = table.rows[-1].cells[col]
            p = cell.paragraphs[0]._p
            set_text(p, f"{_ordinal(now.day)} {now.strftime('%b %Y')}")
            return


def _drop_unused_pictures(document) -> None:
    """The blueprint's own graphs and screenshots are no longer shown: leave them out of the file."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    used = {b.get(_qn("r:embed")) for b in document.element.body.iter(_qn("a:blip"))}
    used |= {b.get(_qn("r:id")) for b in document.element.body.iter("{urn:schemas-microsoft-com:vml}imagedata")}
    rels = document.part.rels
    for rid in [rid for rid, rel in rels.items() if rel.reltype == RT.IMAGE and rid not in used]:
        rels.pop(rid)


def _refresh_fields_on_open(document) -> None:
    """Ask Word to refresh the table of contents (and other fields) when the report is opened."""
    from docx.oxml import OxmlElement
    settings = document.settings.element
    if settings.find(_qn("w:updateFields")) is None:
        el = OxmlElement("w:updateFields")
        el.set(_qn("w:val"), "true")
        settings.append(el)
