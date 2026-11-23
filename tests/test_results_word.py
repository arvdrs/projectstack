"""
The performance report as a Word document: the built-in layout, a company template's
placeholders and [[REPORT]] marker, .dotx templates, self-numbering heading styles,
and that the tables are valid WordprocessingML (element order Word insists on).
"""

import io
import json
import zipfile

import pytest

pytest.importorskip("docx")
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from projectstack.results import word
from tests.test_results_highlights import stepped_doc


@pytest.fixture
def env(tmp_path):
    runs = tmp_path / "runs"
    folder = runs / "20261112-065424-93ae"
    folder.mkdir(parents=True)
    doc = stepped_doc()
    (folder / "findings.json").write_text(json.dumps(doc))
    return {"settings": {"runs_dir": str(runs)}, "folder": folder, "doc": doc}


def text_of(document) -> str:
    return "\n".join(p.text for p in document.paragraphs)


def headings(document) -> list[str]:
    return [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]


def test_built_in_layout(env):
    data, warnings = word.build(env["folder"], env["doc"], settings=env["settings"])
    assert warnings == []
    d = Document(io.BytesIO(data))
    h = headings(d)
    assert h[:5] == ["1. Executive Summary", "2. Test Scope", "3. Load Profile & Test Approach",
                     "4. Performance Highlights & Observations", "5. Response Times"]
    text = text_of(d)
    assert "Performance Test Report" in text and "Portal-500users" in text                  # cover page
    assert "Below are the high-level statistics from the test:" in text and "Maximum users: 500" in text
    assert "The test was executed on October 28, 2026, 08:09 - 09:28 AM UTC." in text
    assert len(d.inline_shapes) >= 1                                                          # the timeline chart
    footer = d.sections[0].footer._element.xml
    assert 'w:instr="PAGE"' in footer
    assert d.tables[0].rows[0].cells[0].text == "Key figure"


def test_company_template_placeholders_and_marker(env):
    starter = word.starter_template()
    t = Document(io.BytesIO(starter))
    t.add_paragraph("Prepared for {{client}} by the performance team")               # not a ProjectStack placeholder
    buf = io.BytesIO(); t.save(buf)
    info = word.save_template(env["settings"], "Acme report.docx", buf.getvalue())
    assert info["marker"] and "scenario" in info["placeholders"] and info["unknown"] == ["client"]
    assert word.template_info(env["settings"])["name"] == "Acme report.docx"

    data, warnings = word.build(env["folder"], env["doc"], settings=env["settings"])
    d = Document(io.BytesIO(data))
    text = text_of(d)
    assert word.MARKER not in text and "{{scenario}}" not in text and "Portal-500users" in text
    assert "Test window: 08:09 - 09:28 AM UTC" in text and "Verdict: No SLA defined" in text
    assert "Prepared for {{client}}" in text and "client" in warnings[0]                     # unknown: kept, reported
    order = [p.text for p in d.paragraphs]
    assert order.index("1. Executive Summary") < order.index("Prepared for {{client}} by the performance team")
    assert d.sections[0].header.paragraphs[0].text == "Portal-500users · Performance Test Report"
    assert d.sections[0].footer.paragraphs[0].text.startswith("Confidential · ")
    assert "Performance Test Report" in text and "Upload your company's Word template" not in text  # its cover, not ours

    word.remove_template(env["settings"])
    assert word.template_info(env["settings"]) is None


def test_template_without_marker_gets_the_report_at_the_end(env):
    t = Document()
    t.add_paragraph("ACME Corp — {{scenario}}")
    buf = io.BytesIO(); t.save(buf)
    assert word.save_template(env["settings"], "t.docx", buf.getvalue())["marker"] is False
    d = Document(io.BytesIO(word.build(env["folder"], env["doc"], settings=env["settings"])[0]))
    paras = [p.text for p in d.paragraphs]
    assert paras[0] == "ACME Corp — Portal-500users" and paras.index("1. Executive Summary") > 0


def test_dotx_and_bad_files(env):
    raw = zipfile.ZipFile(io.BytesIO(word.starter_template()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for item in raw.infolist():
            body = raw.read(item.filename)
            if item.filename == "[Content_Types].xml":
                body = body.replace(word._DOCX, word._DOTX)
            z.writestr(item, body)
    assert word.save_template(env["settings"], "brand.dotx", out.getvalue())["marker"] is True
    Document(str(word.template_path(env["settings"])))                                       # stored as a .docx
    with pytest.raises(word.WordError, match="not a Word document"):
        word.save_template(env["settings"], "notes.txt", b"hello")
    with pytest.raises(word.WordError, match="empty"):
        word.save_template(env["settings"], "x.docx", b"")


def test_self_numbering_headings_get_no_number_of_their_own(env):
    t = Document()
    ppr = t.styles["Heading 1"].element.get_or_add_pPr()
    num = OxmlElement("w:numPr")
    ilvl, num_id = OxmlElement("w:ilvl"), OxmlElement("w:numId")
    ilvl.set(qn("w:val"), "0"); num_id.set(qn("w:val"), "1")
    num.append(ilvl); num.append(num_id); ppr.append(num)
    t.add_paragraph(word.MARKER)
    buf = io.BytesIO(); t.save(buf)
    assert word.save_template(env["settings"], "numbered.docx", buf.getvalue())["auto_numbered"] is True
    d = Document(io.BytesIO(word.build(env["folder"], env["doc"], settings=env["settings"])[0]))
    assert headings(d)[0] == "Executive Summary"


def test_tables_keep_schema_order_without_a_table_style(env):
    t = Document()
    t.styles["Table Grid"].delete()                                                           # a template without it
    t.add_paragraph(word.MARKER)
    buf = io.BytesIO(); t.save(buf)
    word.save_template(env["settings"], "plain.docx", buf.getvalue())
    d = Document(io.BytesIO(word.build(env["folder"], env["doc"], settings=env["settings"])[0]))
    tbl_pr = d.tables[0]._tbl.tblPr
    children = [c.tag.split("}")[1] for c in tbl_pr]
    assert "tblBorders" in children and children.index("tblBorders") < children.index("tblLook")
    tc_pr = d.tables[0].rows[0].cells[0]._tc.tcPr
    tags = [c.tag.split("}")[1] for c in tc_pr]
    assert tags.index("tcW") < tags.index("shd")
