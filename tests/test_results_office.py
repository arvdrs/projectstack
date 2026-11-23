"""
Word 97–2003 (.doc) through LibreOffice: finding it, the conversion command (headless, a
throwaway profile, the right export filter), failures, .doc company templates, and a real
conversion when LibreOffice is installed. A stand-in soffice script records its arguments and
writes the output LibreOffice would, so the plumbing is tested without LibreOffice.
"""

import io
import json
import os
import stat
import sys

import pytest

pytest.importorskip("docx")
from docx import Document

from projectstack.results import office, word
from tests.test_results_highlights import stepped_doc

FAKE = '''#!{python}
import json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path(os.environ["FAKE_SOFFICE_LOG"]).write_text(json.dumps(args))
if os.environ.get("FAKE_SOFFICE_FAIL"):
    print("Error: source file could not be loaded", file=sys.stderr)
    sys.exit(1)
target = args[args.index("--convert-to") + 1].split(":")[0]
out = pathlib.Path(args[args.index("--outdir") + 1])
src = pathlib.Path(args[-1])
body = pathlib.Path(os.environ["FAKE_SOFFICE_DOCX"]).read_bytes() if target == "docx" else b"\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1fake-doc"
(out / (src.stem + "." + target)).write_bytes(body)
'''


@pytest.fixture
def fake_soffice(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "soffice"
    script.parent.mkdir()
    script.write_text(FAKE.replace("{python}", sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    starter = tmp_path / "starter.docx"
    starter.write_bytes(word.starter_template())
    log = tmp_path / "soffice-args.json"
    monkeypatch.setenv("FAKE_SOFFICE_LOG", str(log))
    monkeypatch.setenv("FAKE_SOFFICE_DOCX", str(starter))
    monkeypatch.delenv("FAKE_SOFFICE_FAIL", raising=False)
    return {"settings": {"libreoffice_path": str(script), "runs_dir": str(tmp_path / "runs")}, "log": log,
            "path": str(script)}


@pytest.fixture
def run(tmp_path):
    folder = tmp_path / "runs" / "20261112-065424-93ae"
    folder.mkdir(parents=True)
    doc = stepped_doc()
    (folder / "findings.json").write_text(json.dumps(doc))
    return folder, doc


def test_finds_libreoffice_and_converts_headless(fake_soffice):
    s = fake_soffice["settings"]
    assert office.find_soffice(s) == fake_soffice["path"] and office.status(s)["available"]
    out = office.convert(b"PK fake docx", "docx", "doc", s)
    assert out.startswith(word.OLE_MAGIC)
    args = json.loads(fake_soffice["log"].read_text())
    assert "--headless" in args and "--norestore" in args and "doc:MS Word 97" in args
    profile = next(a for a in args if a.startswith("-env:UserInstallation=file://"))
    assert "pstack-office-" in profile                                   # a throwaway profile, never the user's


def test_conversion_failures_are_explained(fake_soffice, monkeypatch):
    monkeypatch.setenv("FAKE_SOFFICE_FAIL", "1")
    with pytest.raises(office.OfficeError, match="could not convert the file to .doc .*could not be loaded"):
        office.convert(b"x", "docx", "doc", fake_soffice["settings"])
    monkeypatch.setattr(office, "find_soffice", lambda settings=None: None)
    with pytest.raises(office.OfficeError, match="need LibreOffice.*libreoffice-writer-nogui"):
        office.convert(b"x", "docx", "doc", {})
    assert office.status({})["available"] is False and "apt install" in office.status({})["install"]


def test_doc_template_is_converted_on_upload(fake_soffice, monkeypatch):
    s = fake_soffice["settings"]
    info = word.save_template(s, "ACME report.doc", word.OLE_MAGIC + b"old binary word file")
    assert info["name"] == "ACME report.doc" and info["marker"] is True               # the converted .docx
    assert "docx:MS Word 2007 XML" in json.loads(fake_soffice["log"].read_text())
    Document(str(word.template_path(s)))
    monkeypatch.setattr(office, "find_soffice", lambda settings=None: None)
    with pytest.raises(word.WordError, match="save it as .docx"):
        word.save_template(s, "old.doc", word.OLE_MAGIC + b"x")


def test_report_as_doc(fake_soffice, run):
    folder, doc = run
    data, warnings = word.build_doc(folder, doc, settings=fake_soffice["settings"])
    assert data.startswith(word.OLE_MAGIC) and warnings == []
    assert "doc:MS Word 97" in json.loads(fake_soffice["log"].read_text())


@pytest.mark.skipif(office.find_soffice() is None, reason="LibreOffice not installed")
def test_real_libreoffice_round_trip(run, tmp_path):
    folder, doc = run
    settings = {"runs_dir": str(tmp_path / "runs")}
    data, _ = word.build_doc(folder, doc, settings=settings)
    assert data.startswith(word.OLE_MAGIC) and len(data) > 20_000                  # a real Word 97–2003 file
    back = office.convert(data, "doc", "docx", settings)
    text = "\n".join(p.text for p in Document(io.BytesIO(back)).paragraphs)
    assert "Performance Highlights & Observations" in text and "Portal-500users" in text
