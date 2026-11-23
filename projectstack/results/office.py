"""
Word 97–2003 (.doc) through LibreOffice.

ProjectStack writes Word reports as .docx (python-docx); the legacy binary .doc format has no
Python writer, so LibreOffice converts them, headless: the report to .doc, and a company
template saved as .doc to .docx before it is used. Each conversion runs with its own throwaway
LibreOffice profile, so it never clashes with a LibreOffice window that is open, or with another
conversion running at the same time.

LibreOffice is found from the libreoffice_path setting, then the PATH (soffice, libreoffice),
then the usual install folders. On Debian and Ubuntu:

    sudo apt install --no-install-recommends libreoffice-writer-nogui
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

INSTALL_HINT = "sudo apt install --no-install-recommends libreoffice-writer-nogui"
TIMEOUT_S = 180
# LibreOffice export filters by target extension
FILTERS = {"doc": "doc:MS Word 97", "docx": "docx:MS Word 2007 XML", "pdf": "pdf:writer_pdf_Export"}
_CANDIDATES = (
    "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "C:/Program Files/LibreOffice/program/soffice.exe",
)


class OfficeError(RuntimeError):
    """LibreOffice is missing or a conversion failed, explained for the user."""


def find_soffice(settings: dict[str, str] | None = None) -> str | None:
    configured = (settings or {}).get("libreoffice_path") or ""
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for path in _CANDIDATES:
        if Path(path).is_file():
            return path
    for folder in sorted(Path("/opt").glob("libreoffice*/program"), reverse=True):
        if (folder / "soffice").is_file():
            return str(folder / "soffice")
    return None


def status(settings: dict[str, str] | None = None) -> dict:
    path = find_soffice(settings)
    return {"available": path is not None, "path": path, "install": INSTALL_HINT}


def convert(data: bytes, source_ext: str, target_ext: str, settings: dict[str, str] | None = None) -> bytes:
    """`data` (a .docx or .doc file's bytes) converted to `target_ext` ("doc" or "docx") by LibreOffice."""
    soffice = find_soffice(settings)
    if soffice is None:
        raise OfficeError(f"Word 97–2003 (.doc) files need LibreOffice, which is not installed — run: {INSTALL_HINT} "
                          "(or set libreoffice_path in Settings)")
    if target_ext not in FILTERS:
        raise OfficeError(f"Cannot convert to .{target_ext}")
    with tempfile.TemporaryDirectory(prefix="pstack-office-") as tmp:
        work = Path(tmp)
        source = work / f"report.{source_ext}"
        source.write_bytes(data)
        out_dir = work / "out"
        out_dir.mkdir()
        profile = (work / "profile").as_uri()
        cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--invisible", "--norestore",
               "--nolockcheck", "--nodefault", "--convert-to", FILTERS[target_ext], "--outdir", str(out_dir), str(source)]
        env = {**os.environ, "HOME": str(work)}          # LibreOffice writes caches under HOME
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
        except subprocess.TimeoutExpired as exc:
            raise OfficeError(f"LibreOffice did not finish converting within {TIMEOUT_S}s") from exc
        except OSError as exc:
            raise OfficeError(f"Could not start LibreOffice ({soffice}): {exc}") from exc
        target = out_dir / f"report.{target_ext}"
        if not target.is_file() or target.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
            raise OfficeError(f"LibreOffice could not convert the file to .{target_ext} ({detail[0][:200]})")
        return target.read_bytes()
