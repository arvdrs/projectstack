"""
A small .xlsx writer: sheets of plain tables (a bold header row, text and numbers, a number
format per column), without a spreadsheet library. Opens in Excel, LibreOffice and Google
Sheets. Strings are written inline, so no shared-strings part is needed.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

# Built-in number formats (ECMA-376 §18.8.30): no custom formats needed
_BUILTIN_FORMATS = {None: 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10}
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BAD_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")


@dataclass
class Sheet:
    name: str
    header: list[str]
    rows: list[list[Any]]                         # str, int, float or None per cell
    formats: list[str | None] = field(default_factory=list)   # per column, one of _BUILTIN_FORMATS
    widths: list[float] = field(default_factory=list)          # per column, in characters
    bold_rows: set[int] = field(default_factory=set)           # indexes into rows (the header is always bold)


def _col(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def _text(value: str) -> str:
    return escape(_BAD_XML.sub("", value))


def _sheet_xml(sheet: Sheet, style: dict[tuple[int, bool], int]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0">'
           '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
           '</sheetView></sheetViews>']
    if sheet.widths:
        out.append("<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                                      for i, w in enumerate(sheet.widths)) + "</cols>")
    out.append("<sheetData>")
    for r, cells in enumerate([sheet.header, *sheet.rows]):
        bold = r == 0 or (r - 1) in sheet.bold_rows
        out.append(f'<row r="{r + 1}">')
        for c, value in enumerate(cells):
            ref = f"{_col(c)}{r + 1}"
            fmt = 0 if r == 0 else _BUILTIN_FORMATS[sheet.formats[c] if c < len(sheet.formats) else None]
            s = style[(fmt, bold)]
            if value is None:
                out.append(f'<c r="{ref}" s="{s}"/>')
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or (
                    isinstance(value, float) and not math.isfinite(value)):
                text = _text(str(value))
                space = ' xml:space="preserve"' if text != text.strip() else ""
                out.append(f'<c r="{ref}" s="{s}" t="inlineStr"><is><t{space}>{text}</t></is></c>')
            else:
                out.append(f'<c r="{ref}" s="{s}"><v>{value!r}</v></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def workbook(sheets: list[Sheet]) -> bytes:
    """The .xlsx file for these sheets."""
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")
    names: list[str] = []
    for sheet in sheets:
        name = _BAD_SHEET_NAME.sub(" ", sheet.name).strip("' ")[:31] or "Sheet"
        base, n = name, 2
        while name.lower() in (x.lower() for x in names):
            name = f"{base[:28]} {n}"
            n += 1
        names.append(name)

    # One cell style per (number format, bold) pair in use
    style: dict[tuple[int, bool], int] = {}
    for fmt in sorted({_BUILTIN_FORMATS[f] for s in sheets for f in [None, *s.formats]}):
        for bold in (False, True):
            style[(fmt, bold)] = len(style)
    apply_fmt, apply_font = ' applyNumberFormat="1"', ' applyFont="1"'     # (no backslashes in f-strings on 3.10)
    xfs = "".join(f'<xf numFmtId="{fmt}" fontId="{int(bold)}" fillId="0" borderId="0" xfId="0"'
                  f'{apply_fmt if fmt else ""}{apply_font if bold else ""}/>'
                  for (fmt, bold) in style)
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              f'<cellXfs count="{len(style)}">{xfs}</cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              '</styleSheet>')

    n = len(sheets)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                  for i in range(n))
        + "</Types>")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>')
    book = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                  for i, name in enumerate(names))
        + "</sheets></workbook>")
    book_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i + 1}" '
                  'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                  f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
        + f'<Relationship Id="rId{n + 1}" '
          'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        + "</Relationships>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", book_rels)
        z.writestr("xl/styles.xml", styles)
        for i, sheet in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet, style))
    return buf.getvalue()
