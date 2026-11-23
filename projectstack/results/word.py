"""
The performance report as a Word document (.docx), built inside the company's own template.

With a template (uploaded in Settings → Report template, kept next to the runs folder as
templates/report_template.docx), the report keeps the template's look: its styles (headings,
body text, lists, tables, captions), page setup, cover page, header, footer and logo.

  {{placeholders}}   anywhere in the template's text — cover page, header, footer, tables — are
                     filled in: see PLACEHOLDERS. Unknown ones are left as they are and reported.
  [[REPORT]]         a paragraph holding only this marks where the report goes (it replaces the
                     paragraph). Without it the report follows the template's own content, after a
                     page break.

Headings use the template's Heading 1–3 styles and are numbered in the text ("4. Performance
Highlights & Observations"), unless the template's Heading 1 numbers itself. Tables use its
"Table Grid" style when it has one (else plain borders); bullets its "List Bullet" style.
Without a template, a built-in layout with a cover page is used.

The content is the technical report's: the same narrative, highlights, figures, findings,
server metrics, screenshots and their comparison with the load test. Charts are pictures.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from projectstack.results import fmt

MARKER = "[[REPORT]]"
TEMPLATE_NAME = "report_template.docx"
INFO_NAME = "report_template.json"
MAX_TEMPLATE_BYTES = 20 * 1024 * 1024
_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_DOTX = b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
_DOCX = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"

PLACEHOLDERS = {
    "scenario": "Scenario name", "run_id": "Run id", "tag": "Build or tag", "test_date": "Date of the test",
    "test_window": "When the test ran (UTC)", "duration": "Length of the run", "peak_users": "Peak concurrent users",
    "region": "Region (test context)", "test_type": "Test type", "verdict": "Verdict (Passed, At risk, Failed …)",
    "total_requests": "Requests over the whole run", "error_rate": "Error rate (requests, steady state)",
    "throughput": "Average throughput (hit/sec)", "p95": "p95 response time (steady state)",
    "report_date": "Date the report was generated", "generated_by": "ProjectStack",
}


class WordError(RuntimeError):
    """A problem with the Word report or its template, explained for the user."""


# ---------------------------------------------------------------------------
# The company template
# ---------------------------------------------------------------------------

def template_dir(settings: dict[str, str]) -> Path:
    return Path(settings["runs_dir"]).expanduser().parent / "templates"


def template_path(settings: dict[str, str]) -> Path | None:
    path = template_dir(settings) / TEMPLATE_NAME
    return path if path.is_file() else None


def _as_docx(data: bytes) -> bytes:
    """A .dotx is a .docx with another content type: python-docx opens only the latter."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
        names = zin.namelist()
    except zipfile.BadZipFile as exc:
        raise WordError("That file is not a Word document (.doc, .docx or .dotx)") from exc
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise WordError("That file is not a Word document (.doc, .docx or .dotx)")
    types = zin.read("[Content_Types].xml")
    if _DOTX not in types:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            body = zin.read(item.filename)
            zout.writestr(item, body.replace(_DOTX, _DOCX) if item.filename == "[Content_Types].xml" else body)
    return out.getvalue()


def _open(data: bytes):
    from docx import Document
    try:
        return Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — any parse problem is the same thing for the user
        raise WordError(f"Word could not open that template: {exc}") from exc


def _parts_xml(document) -> list:
    """The XML of the body and of every header and footer (first page, even pages …)."""
    out = [document.element.body]
    for part in document.part.package.iter_parts():
        name = str(part.partname)
        if name.startswith(("/word/header", "/word/footer")) and hasattr(part, "element"):
            out.append(part.element)
    return out


def _paragraph_text(p) -> str:
    from docx.oxml.ns import qn
    return "".join(t.text or "" for t in p.iter(qn("w:t")))


def describe(data: bytes, name: str = "") -> dict[str, Any]:
    """What a template offers: its placeholders, whether it marks where the report goes, its styles."""
    from docx.oxml.ns import qn
    document = _open(_as_docx(data))
    found: set[str] = set()
    marker = False
    for root in _parts_xml(document):
        for p in root.iter(qn("w:p")):
            text = _paragraph_text(p)
            found.update(_TOKEN_RE.findall(text))
            marker = marker or text.strip() == MARKER
    styles = {s.name for s in document.styles}
    return {"name": name, "bytes": len(data), "placeholders": sorted(found),
            "unknown": sorted(found - set(PLACEHOLDERS)), "marker": marker,
            "styles": {"headings": all(f"Heading {i}" in styles for i in (1, 2)), "list_bullet": "List Bullet" in styles,
                       "table_grid": "Table Grid" in styles, "caption": "Caption" in styles},
            "auto_numbered": _auto_numbered(document)}


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"     # Word 97–2003 (.doc, .dot) files start with this


def save_template(settings: dict[str, str], name: str, data: bytes) -> dict[str, Any]:
    from projectstack.results import office
    if not data:
        raise WordError("The file is empty")
    if len(data) > MAX_TEMPLATE_BYTES:
        raise WordError(f"The template is larger than {MAX_TEMPLATE_BYTES // (1024 * 1024)} MB")
    if data.startswith(OLE_MAGIC):            # a .doc / .dot template: LibreOffice turns it into a .docx first
        try:
            data = office.convert(data, "doc", "docx", settings)
        except office.OfficeError as exc:
            raise WordError(f"{exc}. Or open the template in Word and save it as .docx.") from exc
    docx_bytes = _as_docx(data)
    info = {**describe(docx_bytes, re.split(r"[\\/]", name or "template.docx")[-1][:120]),
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    folder = template_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / TEMPLATE_NAME).write_bytes(docx_bytes)
    (folder / INFO_NAME).write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def template_info(settings: dict[str, str]) -> dict[str, Any] | None:
    path = template_path(settings)
    if path is None:
        return None
    try:
        return json.loads((path.parent / INFO_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**describe(path.read_bytes(), TEMPLATE_NAME), "uploaded_at": None}


def remove_template(settings: dict[str, str]) -> None:
    folder = template_dir(settings)
    for name in (TEMPLATE_NAME, INFO_NAME):
        (folder / name).unlink(missing_ok=True)


def starter_template() -> bytes:
    """A plain template to brand: a cover page with placeholders, a header and footer, and the marker."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    document = Document()
    document.add_paragraph()
    title = document.add_paragraph("Performance Test Report", style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.add_paragraph("{{scenario}}", style="Subtitle")
    for label, token in (("Test date", "test_date"), ("Test window", "test_window"), ("Peak users", "peak_users"),
                         ("Verdict", "verdict"), ("Report date", "report_date")):
        p = document.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run("{{" + token + "}}")
    document.add_paragraph("Replace this page with your company's cover; keep or move the placeholders. "
                           "The report goes where the marker below is.").runs[0].font.size = Pt(9)
    document.add_page_break()
    document.add_paragraph(MARKER)
    section = document.sections[0]
    section.header.paragraphs[0].text = "{{scenario}} · Performance Test Report"
    section.footer.paragraphs[0].text = "Confidential · {{report_date}}"
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _auto_numbered(document) -> bool:
    """Whether the template's Heading 1 numbers itself (then the text gets no "4." of its own)."""
    from docx.oxml.ns import qn
    try:
        style = document.styles["Heading 1"]
    except KeyError:
        return False
    while style is not None:
        ppr = style.element.find(qn("w:pPr"))
        if ppr is not None and ppr.find(qn("w:numPr")) is not None:
            return True
        style = style.base_style
    return False


# ---------------------------------------------------------------------------
# Writing into the document
# ---------------------------------------------------------------------------

class _Writer:
    def __init__(self, document):
        from docx.shared import Emu
        self.d = document
        self.styles = {s.name for s in document.styles}
        self.numbered = not _auto_numbered(document)
        section = document.sections[-1]
        self.width = Emu(min(int(section.page_width - section.left_margin - section.right_margin), 6480000))  # ≤ 18 cm
        self.figures = 0
        self.section_no = 0

    def _style(self, name: str) -> str | None:
        return name if name in self.styles else None

    def heading(self, text: str, level: int = 1) -> None:
        if level == 1:
            self.section_no += 1
            text = f"{self.section_no}. {text}" if self.numbered else text
        style = self._style(f"Heading {level}")
        if style:
            self.d.add_paragraph(text, style=style)
        else:
            run = self.d.add_paragraph().add_run(text)
            run.bold = True

    def para(self, text: str = "", *, bold_lead: str = "", italic: bool = False, small: bool = False):
        from docx.shared import Pt, RGBColor
        p = self.d.add_paragraph()
        if bold_lead:
            p.add_run(bold_lead).bold = True
        if text:
            run = p.add_run(text)
            run.italic = italic
            if small:
                run.font.size = Pt(8.5)
                run.font.color.rgb = RGBColor(0x52, 0x51, 0x4E)
        return p

    def bullets(self, items: list[str], *, numbered: bool = False) -> None:
        style = self._style("List Number" if numbered else "List Bullet")
        for i, item in enumerate(items, 1):
            if style:
                self.d.add_paragraph(item, style=style)
            else:
                self.d.add_paragraph(f"{f'{i}.' if numbered else '•'} {item}")

    def lead_bullets(self, items: list[tuple[str, str]]) -> None:
        """Bullets with a bold lead: ("Maximum users: ", "500")."""
        style = self._style("List Bullet")
        for lead, text in items:
            p = self.d.add_paragraph(style=style) if style else self.d.add_paragraph("• ")
            p.add_run(lead).bold = True
            p.add_run(text)

    def table(self, headers: list[str], rows: list[list[Any]], *, numeric: set[int] = frozenset(),
              widths: list[float] | None = None, small: bool = False, bold_last: bool = False) -> None:
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt
        table = self.d.add_table(rows=1, cols=len(headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        if self._style("Table Grid"):
            table.style = "Table Grid"
        else:
            borders = OxmlElement("w:tblBorders")
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                b = OxmlElement(f"w:{edge}")
                for k, v in (("val", "single"), ("sz", "4"), ("space", "0"), ("color", "BFBFBF")):
                    b.set(qn(f"w:{k}"), v)
                borders.append(b)
            # Word wants tblPr's children in schema order: borders before shading, layout, margins and look
            table._tbl.tblPr.insert_element_before(borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
                                                   "w:tblCaption", "w:tblDescription")
        size = Pt(8) if small else Pt(9)

        def fill(row, values, header=False, bold=False):
            for i, (cell, value) in enumerate(zip(row.cells, values)):
                cell.text = ""
                p = cell.paragraphs[0]
                run = p.add_run("" if value is None else str(value))
                run.font.size = size
                run.bold = header or bold
                if i in numeric and not header:
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                if header:
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "EDEDEA")
                    cell._tc.get_or_add_tcPr().insert_element_before(
                        shd, "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")

        fill(table.rows[0], headers, header=True)
        flag = OxmlElement("w:tblHeader")                      # repeat the header row on every page
        flag.set(qn("w:val"), "true")
        table.rows[0]._tr.get_or_add_trPr().insert_element_before(flag, "w:tblCellSpacing", "w:jc", "w:hidden",
                                                                  "w:ins", "w:del", "w:trPrChange")
        for k, values in enumerate(rows):
            fill(table.add_row(), values, bold=bold_last and k == len(rows) - 1)
        if widths:
            total = sum(widths)
            for row in table.rows:
                for cell, w in zip(row.cells, widths):
                    cell.width = int(self.width * w / total)
        self.d.add_paragraph()

    def picture(self, data: bytes, caption: str = "") -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        self.d.add_picture(io.BytesIO(data), width=self.width)
        self.d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if caption:
            self.figures += 1
            style = self._style("Caption")
            text = f"Figure {self.figures}: {caption}"
            if style:
                self.d.add_paragraph(text, style=style)
            else:
                self.para(text, italic=True, small=True)

    def page_break(self) -> None:
        self.d.add_page_break()


def _fill_placeholders(document, values: dict[str, str]) -> list[str]:
    """Fill {{name}} everywhere; returns the names that have no value (left as they are)."""
    from docx.oxml.ns import qn
    unknown: set[str] = set()
    for root in _parts_xml(document):
        for p in root.iter(qn("w:p")):
            texts = list(p.iter(qn("w:t")))
            joined = "".join(t.text or "" for t in texts)
            if "{{" not in joined:
                continue

            def sub(m: re.Match) -> str:
                if m.group(1) in values:
                    return values[m.group(1)]
                unknown.add(m.group(1))
                return m.group(0)
            new = _TOKEN_RE.sub(sub, joined)
            if new != joined and texts:
                # A placeholder can be split across runs: the paragraph's text goes into its first run
                texts[0].text = new
                texts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                for t in texts[1:]:
                    t.text = ""
    return sorted(unknown)


def _place_at_marker(document, before: set) -> bool:
    """Move what was added after `before` to where the marker paragraph is; False when there is none."""
    from docx.oxml.ns import qn
    body = document.element.body
    marker = next((p for p in body.iter(qn("w:p")) if p in before and _paragraph_text(p).strip() == MARKER), None)
    if marker is None:
        return False
    added = [el for el in body if el not in before and el.tag != qn("w:sectPr")]
    for el in added:
        marker.addprevious(el)
    marker.getparent().remove(marker)
    return True


def _page_number_footer(document) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    p = document.sections[0].footer.paragraphs[0]
    p.text = "Page "
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    p._p.append(field)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def values_for(doc: dict[str, Any], ctx: dict[str, Any], facts: dict[str, Any], verdict: str) -> dict[str, str]:
    run = doc["run"]
    tot = facts.get("totals") or {}
    rt = facts.get("response_times") or {}
    window = facts.get("window", {}).get("text", "")
    date, _, span = window.rpartition(", ")
    return {
        "scenario": run.get("scenario", ""), "run_id": run.get("run_id", ""), "tag": run.get("tag", ""),
        "test_date": date or fmt.day(run.get("start", "")), "test_window": span or window,
        "duration": fmt.duration(run.get("duration_s")), "peak_users": fmt.num(run.get("peak_users")),
        "region": ctx.get("region") or "", "test_type": facts.get("test_type") or "", "verdict": verdict,
        "total_requests": fmt.num(tot.get("requests")), "error_rate": fmt.pct(tot.get("error_pct")),
        "throughput": f"{tot.get('throughput_per_s', 0):,.2f} hit/sec" if tot.get("throughput_per_s") is not None else "",
        "p95": fmt.ms(rt.get("p95_ms")), "report_date": datetime.now().strftime("%d %B %Y"),
        "generated_by": "ProjectStack",
    }


def build(folder: Path, doc: dict[str, Any], *, settings: dict[str, str], use_llm: bool = False,
          provider_spec: str | None = None, progress: Callable[[str], None] = lambda _: None) -> tuple[bytes, list[str]]:
    """(.docx bytes, warnings): the technical report in the company template, or the built-in layout."""
    from docx import Document
    from projectstack.results import chart_png, highlights as hl, screenshots, server_load
    from projectstack.results.cloud.analysis import read as read_server
    from projectstack.results.report import _server_value, context, get_narrative

    runs_dir = Path(settings["runs_dir"])
    tech, _, warnings = get_narrative(doc, folder, "tech", use_llm=use_llm, provider_spec=provider_spec, strict=False,
                                      fresh=False, settings=settings, progress=progress)
    exec_n, _, w2 = get_narrative(doc, folder, "exec", use_llm=use_llm, provider_spec=provider_spec, strict=False,
                                  fresh=False, settings=settings, progress=progress)
    test_ctx = hl.resolve_context(folder, doc["run"].get("scenario", ""), runs_dir)
    section, _, w3 = hl.get(doc, folder, test_ctx, use_llm=use_llm, provider_spec=provider_spec, strict=False,
                            fresh=False, settings=settings, progress=progress)
    warnings += w2 + w3
    shots = screenshots.report_section(folder, doc, test_ctx, embed=False)
    cloud_rel = server_load.from_cloud(read_server(folder), doc) if doc.get("server") else None
    c = context(doc, tech, "tech", section, shots, cloud_rel)
    facts = hl.facts(doc, test_ctx)
    h = section.content

    tpl = template_path(settings)
    document = Document(str(tpl)) if tpl else Document()
    before = set(document.element.body)
    had_content = bool(tpl) and any(p.text.strip() for p in document.paragraphs)
    w = _Writer(document)
    if not tpl:
        _cover(w, doc, c, facts)
    elif had_content and not any(p.text.strip() == MARKER for p in document.paragraphs):
        w.page_break()

    run = doc["run"]
    # 1. Executive summary
    w.heading("Executive Summary")
    w.para(f" {c['verdict']['label']}" + (f" — SLA: {' · '.join(c['sla'].get('targets') or [])}" if c["sla"].get("defined") else ""),
           bold_lead="Verdict:")
    if exec_n.content.get("headline"):
        w.para(exec_n.content["headline"])
    w.table(["Key figure", "Value", ""], [[k, v, s] for k, v, s in c["tiles"]], numeric={1}, widths=[3, 2, 3])
    if exec_n.content.get("business_impact"):
        w.para(exec_n.content["business_impact"], bold_lead="Business impact: ")
    if exec_n.content.get("recommended_action"):
        w.para(exec_n.content["recommended_action"], bold_lead="Recommended action: ")

    # 2. Scope
    w.heading("Test Scope")
    scope = [["Scenario", run.get("scenario", "")], ["Run", run.get("run_id", "") + (f" · {run['tag']}" if run.get("tag") else "")],
             ["Test window", facts["window"]["text"]], ["Duration", fmt.duration(run.get("duration_s"))],
             ["Test type", facts.get("test_type", "")], ["Peak users", fmt.num(run.get("peak_users"))]]
    if test_ctx.get("region"):
        scope.append(["Region", test_ctx["region"]])
    scope.append(["Samples", fmt.num(run.get("samples"))])
    if c["sla"].get("defined"):
        scope.append(["SLA targets", " · ".join(c["sla"].get("targets") or [])])
    w.table(["Item", "Detail"], scope, widths=[1, 3])
    if test_ctx.get("notes"):
        w.para(test_ctx["notes"], bold_lead="Notes: ")

    # 3. Load profile
    w.heading("Load Profile & Test Approach")
    for s in h["intro"][1:] or h["intro"]:
        w.para(s)
    steady = c["steady"]
    w.para(steady.get("note") or f"Steady state from {fmt.offset(steady.get('start_offset_s'))} to "
           f"{fmt.offset(steady.get('end_offset_s'))}.", bold_lead="Steady state: ")
    png = chart_png.timeline_png(doc)
    if png:
        w.picture(png, "Active users, throughput, p95 response time and error rate over the run (steady state and "
                       "finding windows shaded)")
    for o in tech.content.get("timeline") or []:
        w.bullets([o])

    # 4. Highlights
    w.heading("Performance Highlights & Observations")
    for s in h["intro"]:
        w.para(s)
    w.para("Below are the high-level statistics from the test:")
    w.lead_bullets([(f"{s['name']}: ", s["value"]) for s in h["stats"]])
    w.bullets(h["observations"])
    if section.source == "llm":
        w.para(f"Written by {section.provider} from the results and the test notes; {c['h_check']}.", italic=True, small=True)

    # 5. Response times
    w.heading("Response Times")
    if c["sla"].get("defined"):
        w.heading("SLA results", 2)
        w.table(["Check", "Scope", "Target", "Measured", "Result"],
                [[x["metric"].replace("_", " "), x.get("label") or "All requests", f"{fmt.num(x['target'], 2)} {x['unit']}",
                  f"{fmt.num(x['actual'], 2)} {x['unit']}", "Pass" if x["passed"] else "Fail"]
                 for x in c["sla"].get("checks") or []], numeric={2, 3}, widths=[2, 3, 1.4, 1.4, 1])
    for t in c["tables"]:
        w.heading(t["title"], 2)
        rows = [[r["label"], fmt.num(r["samples"]), fmt.num(r["avg_ms"]), fmt.num(r["p50_ms"]), fmt.num(r["p90_ms"]),
                 fmt.num(r["p95_ms"]), fmt.num(r["p99_ms"]), fmt.num(r["max_ms"]), fmt.pct(r["error_pct"]),
                 fmt.num(r["tps"], 2)] for r in t["rows"]]
        o = t.get("overall")
        if o:
            rows.append([f"All {t['title'].lower()}", fmt.num(o["samples"]), fmt.num(o["avg_ms"]), fmt.num(o["p50_ms"]),
                         fmt.num(o["p90_ms"]), fmt.num(o["p95_ms"]), fmt.num(o["p99_ms"]), fmt.num(o["max_ms"]),
                         fmt.pct(o["error_pct"]), fmt.num(o["tps"], 2)])
        w.table(["Label", "Samples", "Avg", "p50", "p90", "p95", "p99", "Max", "Errors", "Per s"], rows,
                numeric=set(range(1, 10)), widths=[5, 1.3, 1, 1, 1, 1, 1, 1.1, 1.1, 1], small=True, bold_last=bool(o))
        w.para("Times in ms over the steady state; percentiles are exact.", italic=True, small=True)
    b = c.get("baseline")
    if b and b.get("found"):
        w.heading("Compared with the baseline", 2)
        load = b.get("load") or {}
        tag = f" ({b['tag']})" if b.get("tag") else ""
        w.para(f"Baseline run {b.get('run_id')}{tag}. Load "
               f"{fmt.num(load.get('baseline_users'))} → {fmt.num(load.get('current_users'))} users — "
               f"{'comparable' if load.get('comparable') else 'not comparable (' + str(load.get('reason')) + ')'}. "
               f"{b['summary']['regressed']} regressed, {b['summary']['improved']} improved, "
               f"{b['summary']['compared']} labels compared.")
        for f in c["regressions"]:
            p95 = f["evidence"].get("metric") == "p95_ms"
            w.table(["Label", "Baseline", "Now", "Change"],
                    [[r["label"], fmt.ms(r.get("p95_before")) if p95 else fmt.pct(r.get("err_before")),
                      fmt.ms(r.get("p95_after")) if p95 else fmt.pct(r.get("err_after")),
                      f"+{fmt.num(r.get('increase_pct'), 1)}%" if p95 else f"+{fmt.num(r.get('increase_pp'), 2)} pt"]
                     for r in f["evidence"].get("labels", [])], numeric={1, 2, 3}, widths=[4, 1.3, 1.3, 1.2])

    # 6. Server metrics
    if c.get("server") or shots:
        w.heading("Server Metrics")
        server = c.get("server")
        if server:
            for r in [r for r in server.get("resources", []) if r.get("metrics")]:
                w.heading(f"{r['name']} ({r['type']}{', ' + r['role'] if r.get('role') else ''})", 3)
                w.table(["Metric", "Steady-state average", "Peak", "Peak at"],
                        [[m["label"], _server_value(m["steady_avg"], m["unit"]), _server_value(m["max"], m["unit"]),
                          fmt.offset(m["max_offset_s"])] for m in r["metrics"]], numeric={1, 2}, widths=[3, 2, 1.5, 1.5])
            _relation(w, doc, cloud_rel, chart_png, approximate=False)
        if shots:
            w.heading("Server metrics from screenshots", 2)
            if shots["analysed"]:
                w.bullets(shots["observations"])
                if shots["caveats"]:
                    w.para(" ".join(shots["caveats"]), bold_lead="Caveats: ", small=True)
            for pic in shots["pictures"]:
                data, _ = screenshots.shrink((folder / pic["link"]).read_bytes(), "image/png", 1600, 3 * 1024 * 1024) \
                    if (folder / pic["link"]).exists() else (b"", "")
                if data:
                    w.picture(data, pic["heading"])
                if pic.get("time_axis") or pic.get("test_on_chart"):
                    w.para(" · ".join(x for x in (f"Chart shows {pic['time_axis']}" if pic.get("time_axis") else "",
                                                  f"test {pic['test_on_chart']}" if pic.get("test_on_chart") else "") if x),
                           small=True)
                w.bullets(pic.get("observations") or [])
                if pic.get("error"):
                    w.para(f"Not read by the AI: {pic['error']}", italic=True, small=True)
                elif pic.get("caveat"):
                    w.para(pic["caveat"], italic=True, small=True)
            if shots["analysed"]:
                w.para(f"Read by {shots['provider']}{' on this machine' if shots['local'] else ''}. Figures about the "
                       "servers are read from the images by the AI; check them against the screenshots.", italic=True,
                       small=True)
                _relation(w, doc, shots.get("relation"), chart_png, approximate=True)

    # 7. Findings and root causes
    w.heading("Findings")
    if not c["findings"]:
        w.para("No findings: the detectors that ran found no SLA breach, change point, error burst, saturation or "
               "load-generator problem.")
    for f in c["findings"]:
        where = ""
        if f.get("start_offset_s") is not None:
            where = (f"{fmt.offset(f['start_offset_s'])} to {fmt.offset(f['end_offset_s'])}"
                     if f.get("end_offset_s") is not None else f"from {fmt.offset(f['start_offset_s'])}")
        w.heading(f"#{f['n']} {f['sev_label']}: {f['title']}", 3)
        if where or f.get("labels"):
            w.para(" · ".join(x for x in (where, ", ".join(f.get("labels") or [])) if x), small=True)
        w.para(f["detail"])
        if f.get("explanation"):
            w.para(f["explanation"], bold_lead="What it means: ")
    if tech.content.get("root_causes"):
        w.heading("Likely root causes", 2)
        for i, rc in enumerate(tech.content["root_causes"], 1):
            p = w.para(bold_lead=f"{i}. {rc['cause']} ")
            p.add_run(f"({rc['confidence']} confidence). {rc['reasoning']}"
                      + (f" To confirm: {rc['how_to_confirm']}" if rc.get("how_to_confirm") else ""))

    # 8. Recommendations
    w.heading("Recommendations")
    if exec_n.content.get("recommended_action"):
        w.para(exec_n.content["recommended_action"], bold_lead="Recommended action: ")
    if tech.content.get("next_tests"):
        w.para("Recommended next tests:")
        w.bullets([f"{t['test']}{' — ' + t['why'] if t.get('why') else ''}" for t in tech.content["next_tests"]],
                  numbered=True)

    # 9. Errors
    if c["errors"]:
        w.heading("Top Errors")
        w.table(["Label", "Code", "Message", "Count", "First", "Last"],
                [[e["label"], e["response_code"], e.get("failure") or e.get("response_message"), fmt.num(e["count"]),
                  fmt.offset(e["first_offset_s"]), fmt.offset(e["last_offset_s"])] for e in c["errors"]],
                numeric={3}, widths=[3, 0.8, 4, 0.9, 1, 1], small=True)
        w.para("Whole run, including ramp-up and ramp-down.", italic=True, small=True)

    # About this report
    w.heading("About this report", 2)
    notes = [f"Steady state: {steady.get('note') or ''}".rstrip(": ")]
    notes += [f"Data: {x}." for x in doc.get("warnings") or []] + list(c["notes"])
    notes.append("Narrative: " + (f"written by {tech.provider} with prompt {tech.prompt}; {c['number_check']}."
                                  if tech.source == "llm" else "rule-based (no AI).")
                 + " Findings, verdict and figures come from deterministic detectors.")
    notes.append(f"Generated by ProjectStack on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
                 f"from run {run.get('run_id')}.")
    w.bullets([n for n in notes if n])

    if tpl and not _place_at_marker(document, before):
        pass                                    # no marker: the report follows the template's content
    unknown = _fill_placeholders(document, values_for(doc, test_ctx, facts, c["verdict"]["label"]))
    if unknown:
        warnings.append(f"The template has placeholders ProjectStack does not know: {', '.join(unknown)}")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue(), warnings


def _relation(w: _Writer, doc: dict[str, Any], rel: dict[str, Any] | None, chart_png, *, approximate: bool) -> None:
    from projectstack.results.server_load import fmt_value
    if not rel:
        return
    w.heading("Server metrics vs the load test", 3)
    w.bullets(rel["observations"])
    png = chart_png.relation_png(doc, rel)
    if png:
        w.picture(png, "The test's active users and p95 response time with the server metrics under them, on one time "
                       "axis (steady state shaded)")

    def cell(r: float | None, label: str | None, flat: bool = False) -> str:
        return "flat" if flat else "—" if r is None else f"{label} · {r:.2f}"
    w.table(["Server metric", "With the load", "With p95", "With errors", "Steady state", "Peak", "Under flat load"],
            [[r["name"], cell(r["r_users"], r["load"], r["flat"]), "—" if r["flat"] else cell(r["r_p95"], r["with_p95"]),
              cell(r["r_errors"], r["with_errors"]), fmt_value(r["steady_avg"], r["unit"]),
              f"{fmt_value(r['peak'], r['unit'])} at {fmt.offset(r['peak_offset_s'])}",
              f"{r['trend']['direction']} ({fmt_value(r['trend']['from'], r['unit'])} → {fmt_value(r['trend']['to'], r['unit'])})"
              if r.get("trend") else "—"] for r in rel["rows"]],
            numeric={4}, widths=[3, 1.6, 1.6, 1.6, 1.2, 1.8, 1.8], small=True)
    w.para("Each server metric is compared minute by minute with the test's active users, p95 response time and error "
           "rate (Pearson r; strong from 0.7, moderate from 0.4). Response times are compared over the steady state, "
           "where the load is flat. Correlation shows what moved together, not what caused it."
           + (" The server values were read off the screenshots by the AI, so they are approximate." if approximate else ""),
           italic=True, small=True)


def _cover(w: _Writer, doc: dict[str, Any], c: dict[str, Any], facts: dict[str, Any]) -> None:
    """The built-in layout's cover page, header and footer."""
    from docx.shared import Pt
    d = w.d
    d.add_paragraph()
    d.add_paragraph("Performance Test Report", style="Title")
    d.add_paragraph(doc["run"].get("scenario", ""), style="Subtitle")
    for label, value in (("Test window", facts["window"]["text"]), ("Peak users", fmt.num(doc["run"].get("peak_users"))),
                         ("Verdict", c["verdict"]["label"]), ("Run", doc["run"].get("run_id", "")),
                         ("Report date", datetime.now().strftime("%d %B %Y"))):
        p = d.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run(value)
    note = d.add_paragraph("Upload your company's Word template in Settings → Report template to use its cover, "
                           "styles, header and footer.")
    note.runs[0].font.size = Pt(8.5)
    d.sections[0].header.paragraphs[0].text = f"{doc['run'].get('scenario', '')} · Performance Test Report"
    _page_number_footer(d)
    d.add_page_break()


def build_doc(folder: Path, doc: dict[str, Any], *, settings: dict[str, str], use_llm: bool = False,
              provider_spec: str | None = None, progress: Callable[[str], None] = lambda _: None) -> tuple[bytes, list[str]]:
    """(.doc bytes, warnings): the same report in Word 97–2003 format, converted by LibreOffice."""
    from projectstack.results import office
    data, warnings = build(folder, doc, settings=settings, use_llm=use_llm, provider_spec=provider_spec,
                           progress=progress)
    progress("Converting the report to Word 97–2003 (.doc) with LibreOffice …")
    try:
        return office.convert(data, "docx", "doc", settings), warnings
    except office.OfficeError as exc:
        raise WordError(str(exc)) from exc
