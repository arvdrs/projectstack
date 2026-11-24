"""
The report graphs as JPEGs, captured from a JMeter HTML dashboard itself, so they are
exactly what the HTML report shows (and what performance reports paste in):

  Running Vusers Graph     "Active Threads Over Time"       (content/pages/OverTime.html)
  Hits Per Second Graph    "Hits Per Second"                (content/pages/Throughput.html)
  Throughput Graph         "Bytes Throughput Over Time"     (content/pages/OverTime.html)
  Response Time Graph      "Response Times Over Time"       (content/pages/OverTime.html)
  Error Graph              "Codes Per Second"               (content/pages/Throughput.html)

Headless Chrome opens the dashboard's pages from disk, expands each panel as a click on
its title does (the dashboard draws most graphs only then) and captures the panel: its
title bar, the graph and the legend. The capture ends just right of the graph, without
the "Zoom" overview and the panel's buttons, as in a report. Nothing else is changed.

Next to the JPEGs, panels.json records which panel each one shows. Captures made with another
set of panels (the Error Graph was "Total Transactions Per Second" at first) do not count as
captured, so they are taken again from the same report when next asked for.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

# slug (URL and file name), heading in a report, dashboard page, panel id ("Infos" name in graph.js)
GRAPHS = [
    ("running-vusers", "Running Vusers Graph", "OverTime.html", "activeThreadsOverTime"),
    ("hits-per-second", "Hits Per Second Graph", "Throughput.html", "hitsPerSecond"),
    ("throughput", "Throughput Graph", "OverTime.html", "bytesThroughputOverTime"),
    ("response-time", "Response Time Graph", "OverTime.html", "responseTimesOverTime"),
    ("errors", "Error Graph", "Throughput.html", "codesPerSecond"),
]
SLUGS = [g[0] for g in GRAPHS]
PANELS = {g[0]: g[3] for g in GRAPHS}
MANIFEST = "panels.json"                       # {slug: panel id} of the JPEGs in a graphs folder
WINDOW_W, WINDOW_H, SCALE = 1920, 1080, 2      # the dashboard's layout on a full-HD screen, at 2× for print
JPEG_QUALITY = 92
_BINARIES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")


class GraphCaptureError(RuntimeError):
    """The graphs could not be captured; the message says why, for the user."""


def find_chrome() -> str | None:
    env = os.environ.get("PSTACK_CHROME")
    if env and shutil.which(env):
        return shutil.which(env)
    return next((p for name in _BINARIES if (p := shutil.which(name))), None)


def slug_from_name(name: str) -> str | None:
    """ "running-vusers.jpg" -> "running-vusers"; None for anything else (the graphs are JPEGs only)."""
    stem, _, ext = name.rpartition(".")
    return stem if stem in SLUGS and ext.lower() in ("jpg", "jpeg") else None


def graph_files(out_dir: Path) -> dict[str, Path]:
    """The captured JPEGs in `out_dir`: all five, or {} when any is missing or was captured from
    another panel than GRAPHS names now."""
    try:
        if json.loads((out_dir / MANIFEST).read_text()) != PANELS:
            return {}
    except (OSError, ValueError):
        return {}
    files = {slug: out_dir / f"{slug}.jpg" for slug in SLUGS}
    return files if all(f.is_file() for f in files.values()) else {}


def zip_graphs(out_dir: Path, prefix: str) -> bytes:
    """The five JPEGs in one ZIP, named like the single downloads."""
    files = graph_files(out_dir)
    if not files:
        raise GraphCaptureError("The graphs have not been captured yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:     # JPEGs are compressed already
        for slug, path in files.items():
            zf.write(path, f"{prefix}_{slug}.jpg")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def capture_graphs(report_dir: Path, out_dir: Path, timeout: float = 120) -> dict[str, Path]:
    """Capture the five graphs of the dashboard in `report_dir` into `out_dir`/<slug>.jpg."""
    pages = report_dir / "content" / "pages"
    if not (pages / "OverTime.html").is_file() or not (pages / "Throughput.html").is_file():
        raise GraphCaptureError("This JMeter report has no graph pages (content/pages is missing)")
    chrome = find_chrome()
    if not chrome:
        raise GraphCaptureError("The graphs are captured with Google Chrome or Chromium, and neither was found. "
                                "Install one (or set PSTACK_CHROME to its path).")
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: dict[str, bytes] = {}
    with _Chrome(chrome) as cdp:
        cdp.send("Emulation.setDeviceMetricsOverride", width=WINDOW_W, height=WINDOW_H,
                 deviceScaleFactor=SCALE, mobile=False)
        deadline = time.monotonic() + timeout
        for page in dict.fromkeys(g[2] for g in GRAPHS):
            cdp.open((pages / page).resolve().as_uri(), deadline)
            for slug, _, pg, panel in GRAPHS:
                if pg == page:
                    shots[slug] = _capture_panel(cdp, panel, deadline)
    (out_dir / MANIFEST).unlink(missing_ok=True)       # not "captured" until all five are in place
    for slug, data in shots.items():
        tmp = out_dir / f".{slug}.jpg.tmp"
        tmp.write_bytes(data)
        tmp.replace(out_dir / f"{slug}.jpg")
    tmp = out_dir / f".{MANIFEST}.tmp"
    tmp.write_text(json.dumps(PANELS, indent=2))
    tmp.replace(out_dir / MANIFEST)
    return graph_files(out_dir)


def _capture_panel(cdp: "_Chrome", panel: str, deadline: float) -> bytes:
    key = panel[0].upper() + panel[1:]
    # Expanded as a click on the title does: Bootstrap shows the body and the dashboard draws the graph
    cdp.js(f"""(() => {{ const b = document.getElementById('body{key}');
        if (b && !b.classList.contains('in')) $(b).collapse('show'); }})()""")
    cdp.wait(f"""(() => {{ const b = document.getElementById('body{key}');
        return document.querySelectorAll('#flot{key} canvas.flot-base').length > 0
            && (!b || (b.classList.contains('in') && !b.classList.contains('collapsing'))); }})()""", deadline,
             f"the {panel} graph was not drawn")
    cdp.js("document.fonts.ready.then(() => true)")
    time.sleep(0.3)                                       # the legend's colour boxes are added after the graph
    # The capture ends where the graph's area ends (the "Zoom" overview and the buttons are to its right).
    # The legend spans the whole panel, so it wraps within that width instead, as in a narrower window.
    rect = cdp.js(f"""(() => {{
        const panel = document.getElementById('{panel}').querySelector('.panel');
        const graph = document.getElementById('flot{key}');
        const width = graph.getBoundingClientRect().right - panel.getBoundingClientRect().left;
        const footer = panel.querySelector('.panel-footer');
        if (footer) {{ footer.style.width = width + 'px'; footer.style.boxSizing = 'border-box'; }}
        const p = panel.getBoundingClientRect();
        return {{x: p.left + scrollX, y: p.top + scrollY, width: width, height: p.height}}; }})()""")
    if not rect or rect["width"] < 100 or rect["height"] < 100:
        raise GraphCaptureError(f"The {panel} panel was not found in the report")
    clip = {k: round(rect[k], 2) for k in ("x", "y", "width", "height")}
    shot = cdp.send("Page.captureScreenshot", format="jpeg", quality=JPEG_QUALITY,
                    clip={**clip, "scale": 1}, captureBeyondViewport=True)
    return base64.b64decode(shot["data"])


class _Chrome:
    """Headless Chrome on a free port, driven over the DevTools protocol; no network, a throwaway profile."""

    def __init__(self, binary: str):
        self.binary = binary
        self.n = 0

    def __enter__(self) -> "_Chrome":
        from websockets.sync.client import connect
        self.profile = Path(tempfile.mkdtemp(prefix="pstack-chrome-"))
        args = [self.binary, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                "--disable-gpu", "--hide-scrollbars", "--mute-audio", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-background-networking", "--disable-sync", "--disable-dev-shm-usage",
                "--host-resolver-rules=MAP * ~NOTFOUND",          # the dashboard is local files only
                f"--window-size={WINDOW_W},{WINDOW_H}", "about:blank"]
        if _needs_no_sandbox():
            args.insert(1, "--no-sandbox")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port = self._port()
            for _ in range(50):
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5))
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page:
                    break
                time.sleep(0.1)
            else:
                raise GraphCaptureError("Chrome opened no page")
            self.ws = connect(page["webSocketDebuggerUrl"], max_size=256 << 20, open_timeout=10)
            self.send("Page.enable")
            self.send("Runtime.enable")
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def _port(self) -> int:
        marker = self.profile / "DevToolsActivePort"
        for _ in range(200):
            if self.proc.poll() is not None:
                raise GraphCaptureError(f"Chrome ({self.binary}) exited at start-up with code {self.proc.returncode}")
            try:
                return int(marker.read_text().splitlines()[0])
            except (OSError, ValueError, IndexError):
                time.sleep(0.05)
        raise GraphCaptureError("Chrome did not start within 10 seconds")

    def __exit__(self, *exc) -> None:
        try:
            if getattr(self, "ws", None):
                self.ws.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            shutil.rmtree(self.profile, ignore_errors=True)

    def send(self, method: str, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=60))
            if msg.get("id") == my:
                if "error" in msg:
                    raise GraphCaptureError(f"Chrome: {method} failed: {msg['error'].get('message')}")
                return msg.get("result", {})

    def js(self, expression: str):
        r = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            detail = r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"].get("text")
            raise GraphCaptureError(f"The JMeter report page failed: {detail}")
        return r.get("result", {}).get("value")

    def wait(self, expression: str, deadline: float, what: str) -> None:
        while not self.js(expression):
            if time.monotonic() > deadline:
                raise GraphCaptureError(f"Timed out: {what}")
            time.sleep(0.1)

    def open(self, url: str, deadline: float) -> None:
        self.send("Page.navigate", url=url)
        self.wait("document.readyState === 'complete' && !!window.jQuery && !!document.querySelector('.flot-chart')",
                  deadline, f"{url.rsplit('/', 1)[-1]} did not load")


def _needs_no_sandbox() -> bool:
    """Chrome's sandbox does not start as root or in most containers."""
    from projectstack.config import IN_DOCKER
    return IN_DOCKER or (hasattr(os, "geteuid") and os.geteuid() == 0)
