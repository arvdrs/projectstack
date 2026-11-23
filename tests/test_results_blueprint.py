"""
"Create a report" from a blueprint (a past report): the per-run subsection is cloned and refilled,
the blueprint's other regions removed, bullets keep their levels and bold figures, the cover month,
version date and table of contents are refreshed, recommendations and conclusion are drafted.
The blueprint here is built in the test (Word list styles, a picture per graph); no real report.
"""

import io
import json
from datetime import datetime

import pytest

pytest.importorskip("docx")
from docx import Document
from docx.shared import Cm

from projectstack.results import blueprint as bp
from tests.test_results_highlights import stepped_doc
from tests.test_results_screenshots import png


def make_blueprint() -> bytes:
    d = Document()
    d.add_paragraph("ACME PORTAL", style="Title")
    d.add_paragraph("March 2025")
    t = d.add_table(rows=2, cols=3)
    for c, v in zip(t.rows[0].cells, ("Version", "Date", "Author")):
        c.text = v
    for c, v in zip(t.rows[1].cells, ("1.0", "03rd Mar 2025", "Tester")):
        c.text = v
    d.add_paragraph("1. Introduction", style="Heading 3")
    d.add_paragraph("Project text that stays as it is.")
    d.add_paragraph("3.5 Workload Distribution", style="Heading 4")
    w = d.add_table(rows=4, cols=4)
    for r, values in enumerate((("Workload", "Workload", "Workload", "US"), ("Id", "Scenario", "Load %", "9"),
                                ("J01", "Search", "50%", "1"), ("J02", "Checkout", "50%", "1"))):
        for c, v in zip(w.rows[r].cells, values):
            c.text = v
    d.add_paragraph("4. Performance Highlights & Observations", style="Heading 3")
    for region in ("US", "EU"):
        d.add_paragraph(f"4.{1 if region == 'US' else 2} Incremental Load Test - {region} region", style="Heading 4")
        d.add_paragraph("The test was executed on March 1, 2025.", style="List Bullet")
        d.add_paragraph("Maximum users: 9", style="List Bullet 2")
        d.add_paragraph("Performance Metrics", style="Heading 5")
        d.add_paragraph(f"The table below gives the detailed response times in the {region} region.")
        d.add_paragraph("ACME - Aggregate Report - Mar 1")
        for graph in ("Running Vusers Graph", "Hits Per Second Graph", "Throughput Graph", "Response Time Graph"):
            d.add_paragraph(graph, style="Heading 5")
            d.add_picture(io.BytesIO(png(300, 120)), width=Cm(15))
            d.add_paragraph(f"Caption of the {graph.lower()}.")
        d.add_paragraph("Server Metrics", style="Heading 5")
        cap = d.add_paragraph()
        cap.add_run("CPU and Memory - Cloud Observability").bold = True
        d.add_picture(io.BytesIO(png(300, 120)), width=Cm(15))
        d.add_paragraph("This chart illustrates CPU and memory.")
    d.add_paragraph("5. Tuning Recommendations", style="Heading 3")
    d.add_paragraph("Old recommendation.", style="List Bullet")
    d.add_paragraph("Old detail.", style="List Bullet 2")
    d.add_paragraph("6. Conclusion", style="Heading 3")
    d.add_paragraph("Old conclusion one.")
    d.add_paragraph("Old conclusion two.")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


@pytest.fixture
def run(tmp_path):
    folder = tmp_path / "runs" / "20261112-065424-93ae"
    folder.mkdir(parents=True)
    doc = stepped_doc()
    (folder / "findings.json").write_text(json.dumps(doc))
    (folder / "context.json").write_text(json.dumps({"region": "us-east-1", "test_type": "", "notes": ""}))
    return folder, doc, {"runs_dir": str(tmp_path / "runs")}


def test_plan_finds_the_parts():
    d = Document(io.BytesIO(make_blueprint()))
    pl = bp.plan(d)
    body = list(d.element.body)
    assert bp._text(body[pl.section4]).startswith("4. Performance Highlights")
    assert [bp._text(body[i]) for i in pl.subsections] == ["4.1 Incremental Load Test - US region",
                                                          "4.2 Incremental Load Test - EU region"]
    assert bp._text(body[pl.month]) == "March 2025"


def test_report_from_one_run(run):
    folder, doc, settings = run
    data, notes = bp.build(make_blueprint(), [bp.RunInput(folder, doc)], settings=settings,
                           report_date=datetime(2026, 11, 17))
    d = Document(io.BytesIO(data))
    texts = [p.text for p in d.paragraphs]
    assert "4.1 Incremental Load Test - US region" in texts and not any("EU region" in t for t in texts)
    assert texts.count("Running Vusers Graph") == 1                                   # one subsection, not two
    assert "November 2026" in texts and "March 2025" not in texts                     # the cover month
    assert d.tables[0].rows[1].cells[1].text == "17th Nov 2026"                       # the version date
    assert "Project text that stays as it is." in texts                               # kept as it is

    intro = next(p for p in d.paragraphs if p.text.startswith("The test was executed on"))
    assert intro.text == "The test was executed on October 28, 2026, 08:09 - 09:28 AM UTC."
    assert intro.style.name == "List Bullet"
    assert any(r.bold and r.text.startswith("October 28, 2026") for r in intro.runs)  # bold figures
    stat = next(p for p in d.paragraphs if p.text.startswith("Maximum users: "))
    assert stat.text == "Maximum users: 500" and stat.style.name == "List Bullet 2"
    assert "Below are the high-level statistics from the test:" in texts

    assert any("detailed response times of all the individual requests" in t and "US region" in t for t in texts)
    assert "ACME - Aggregate Report - Mar 1" not in texts
    placeholder = next(p for p in d.paragraphs if "Attach this run's server metrics screenshots" in p.text)
    assert placeholder.runs[0].font.highlight_color is not None
    assert any("no server metrics screenshots" in n for n in notes)

    rec = [p for p in d.paragraphs if p.style.name.startswith("List Bullet")
           and texts.index(p.text) > texts.index("5. Tuning Recommendations")]
    assert rec and rec[0].text.startswith("Optimize the slowest endpoint:") and rec[0].runs[0].bold
    assert "Old recommendation." not in texts and "Old conclusion one." not in texts
    conclusion = texts[texts.index("6. Conclusion") + 1]
    assert conclusion.startswith("Incremental load testing across US (500 virtual users) was completed on October 28, 2026")
    assert "w:updateFields" in d.settings.element.xml                                 # Word refreshes the contents


def test_two_runs_and_titles(run):
    folder, doc, settings = run
    eu = json.loads(json.dumps(doc))
    eu["run"]["peak_users"] = 200
    data, _ = bp.build(make_blueprint(), [bp.RunInput(folder, doc, "Soak Test - US region"),
                                          bp.RunInput(folder, eu, "Soak Test - EU region", "EU")],
                       settings=settings)
    texts = [p.text for p in Document(io.BytesIO(data)).paragraphs]
    assert "4.1 Soak Test - US region" in texts and "4.2 Soak Test - EU region" in texts
    assert texts.count("Performance Metrics") == 2
    conclusion = texts[texts.index("6. Conclusion") + 1]
    assert "across US, and EU (500, and 200 virtual users, respectively)" in conclusion


def test_not_a_report_blueprint():
    d = Document()
    d.add_paragraph("Some memo", style="Heading 1")
    out = io.BytesIO(); d.save(out)
    with pytest.raises(bp.BlueprintError, match="Performance Highlights"):
        bp.build(out.getvalue(), [bp.RunInput(None, stepped_doc())], settings={"runs_dir": "/tmp"})
    with pytest.raises(bp.BlueprintError, match="at least one"):
        bp.build(out.getvalue(), [], settings={"runs_dir": "/tmp"})
