"""
ProjectStack FastAPI backend.

Provides REST endpoints for the full performance-testing workflow:
  upload HAR → correlate → generate JMX → run test → report
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import io
import json
import os
import pickle
import re
import subprocess
import tempfile
import time
import uuid
import zipfile
from typing import Any, Optional

from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from projectstack.parser.har_parser import parse_har, get_domains
from projectstack.parser.recording_parser import parse_recording
from projectstack.correlator.engine import (
    run_correlation, run_correlation_with_comparison, run_ai_correlation, cv_to_dict, cv_from_dict,
)
from projectstack.correlator.matching import find_encoding, infer_extractor, request_haystack
from projectstack.correlator.confidence import assess as assess_correlation
from projectstack.correlator import rule_packs
from projectstack.generator.jmx_generator import generate_jmx
from projectstack.generator.transaction_grouper import clean_transaction_name, transaction_names
from projectstack.generator.validator import validate_jmx
from projectstack.runner.jmeter_runner import (
    run_jmeter, find_jmeter, read_thread_settings, read_thread_group, stop_jmeter, count_samplers,
    set_jmeter_path, jmeter_info, required_plugin,
)
from projectstack.runner.live_stats import LiveRun
from projectstack.runner.metrics_parser import parse_jtl
from projectstack.analyzer.har_analyzer import analyze_har, scan_sensitive_data, detect_duplicates
from projectstack.analyzer.report_analyzer import (
    parse_html_report, from_session_results, derive_insights, build_llm_context,
)
from projectstack.analyzer.sla import normalize_sla, evaluate_sla
from projectstack.exporter import email_report
from projectstack.exporter.k6_exporter import export_k6
from projectstack.exporter.cicd_exporter import export_github_actions, export_jenkins

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="ProjectStack API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent directory for JMeter HTML reports
REPORTS_BASE_DIR = Path.home() / ".projectstack" / "reports"
REPORTS_BASE_DIR.mkdir(parents=True, exist_ok=True)

from fastapi.staticfiles import StaticFiles  # noqa: E402
app.mount(
    "/api/jmeter-reports",
    StaticFiles(directory=str(REPORTS_BASE_DIR), html=True),
    name="jmeter_reports",
)

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_sessions: dict[str, dict[str, Any]] = {}
_live_runs: dict[str, LiveRun] = {}      # session id -> live stats of its current/last run

# ---------------------------------------------------------------------------
# Session persistence — survives server restarts
# ---------------------------------------------------------------------------

_SESSIONS_DIR = Path.home() / ".projectstack" / "sessions"
_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _save_session(session_id: str) -> None:
    """Persist a session to disk so it survives server restarts."""
    session = _sessions.get(session_id)
    if not session:
        return
    try:
        with open(_SESSIONS_DIR / f"{session_id}.pkl", "wb") as f:
            pickle.dump(session, f)
    except Exception:
        pass


def _load_session_from_disk(session_id: str) -> dict[str, Any] | None:
    """Restore a session from disk into memory."""
    path = _SESSIONS_DIR / f"{session_id}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            session: dict[str, Any] = pickle.load(f)
        _sessions[session_id] = session
        return session
    except Exception:
        return None

from projectstack.config import (  # noqa: E402
    DEFAULT_SETTINGS, SECRET_FIELDS as _SECRET_FIELDS, load_settings, save_settings,
)

_settings: dict[str, str] = dict(DEFAULT_SETTINGS)


def _load_settings() -> None:
    # Environment overrides (CI) are not loaded here, so they are never written back to the file
    _settings.update(load_settings(apply_env=False))
    if _settings.get("anthropic_api_key"):
        os.environ["ANTHROPIC_API_KEY"] = _settings["anthropic_api_key"]
    set_jmeter_path(_settings.get("jmeter_path"))


def _persist_settings() -> None:
    try:
        save_settings(_settings)
    except Exception:
        pass


_load_settings()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    ai_provider: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    jmeter_path: Optional[str] = None
    default_threads: Optional[str] = None
    default_rampup: Optional[str] = None
    default_loops: Optional[str] = None
    grafana_url: Optional[str] = None
    gmail_address: Optional[str] = None
    gmail_app_password: Optional[str] = None


class ShareReportRequest(BaseModel):
    to: list[str] | str
    cc: list[str] | str = []
    subject: str = ""
    note: str = ""
    include_ai: bool = True
    attach_jmeter_report: bool = True
    attach_insights_html: bool = False


class CorrelateRequest(BaseModel):
    # Auto-correlation is the rule-based engine; AI auto-correlation is its own endpoint
    use_ai: bool = False


_LOAD_PROFILES: dict[str, dict] = {
    "smoke":  {"threads": 1,  "rampup": 10,  "loops": 1},
    "soak":   {"threads": 10, "rampup": 60,  "loops": 60},
    "spike":  {"threads": 50, "rampup": 5,   "loops": 5},
    "stress": {"threads": 25, "rampup": 30,  "loops": 20},
}


class UltimateRow(BaseModel):
    threads: int = Field(10, ge=1, le=100000)
    initial_delay: int = Field(0, ge=0)
    startup: int = Field(30, ge=0)
    hold: int = Field(60, ge=0)
    shutdown: int = Field(10, ge=0)


class ThreadGroupConfig(BaseModel):
    """Thread group of the generated script. Only the fields of the chosen type are used."""
    type: Literal["standard", "stepping", "concurrency", "ultimate"] = "standard"
    # stepping
    threads: int = Field(10, ge=1, le=100000)
    initial_delay: int = Field(0, ge=0)
    start_burst: int = Field(0, ge=0)
    step_users: int = Field(2, ge=1)
    step_period: int = Field(30, ge=0)
    step_rampup: int = Field(5, ge=0)
    stop_users: int = Field(5, ge=0)
    stop_period: int = Field(1, ge=0)
    hold: int = Field(60, ge=0)            # stepping + concurrency
    # concurrency
    target: int = Field(10, ge=1, le=100000)
    ramp_up: int = Field(60, ge=0)
    steps: int = Field(0, ge=0)
    iterations: int = Field(0, ge=0)       # 0 = unlimited
    # ultimate
    rows: list[UltimateRow] = []


class SlaConfig(BaseModel):
    """User-defined SLA. Nothing is set by default — then results are never judged pass/fail."""
    response_time_metric: Literal["avg", "p90", "p95", "p99"] = "p90"
    response_time_ms: Optional[float] = None
    transaction_targets: dict[str, Optional[float]] = {}
    error_rate_pct: Optional[float] = None
    throughput_rps: Optional[float] = None


class GenerateRequest(BaseModel):
    test_name: str = "ProjectStack Test"
    threads: int = 1
    rampup: int = 1
    loops: int = 1
    base_url: str = ""
    base_urls: Optional[list[str]] = None  # Multiple base URLs; overrides base_url if set
    test_vars: Optional[list[dict]] = None   # [{name, value}] — P_* User Defined Variables
    test_data_files: Optional[list[dict]] = None  # [{filename, data, accessMethod}]
    include_view_tree: bool = True
    grafana_url: str = ""
    no_transactions: bool = True
    selected_var_names: Optional[list[str]] = None          # None = keep all
    selected_request_indices: Optional[list[int]] = None    # None = include all
    custom_assertions: Optional[dict[str, list]] = None     # "req_idx" -> [{type,value,enabled}]
    custom_transaction_names: Optional[dict[str, str]] = None
    request_order: Optional[list[int]] = None
    include_think_times: bool = False                       # add ConstantTimer from HAR timestamps
    load_profile: str = "custom"                           # smoke | soak | spike | stress | custom
    infinite_loops: bool = False                           # loops=-1 with scheduler
    duration_seconds: int = 0                              # scheduler duration (0 = disabled)
    thread_group: Optional[ThreadGroupConfig] = None       # None = standard Thread Group
    correlation_markers: bool = True                       # fail samples sent with NOT_FOUND (health check)


class RunRequest(BaseModel):
    # Anything left out keeps the value configured in the generated script.
    # Overrides apply to the standard Thread Group only.
    threads: Optional[int] = None
    rampup: Optional[int] = None
    loops: Optional[int] = None              # -1 = loop until the duration ends
    duration_seconds: Optional[int] = None   # 0 = run by loop count
    sla: Optional[SlaConfig] = None          # defined in the Generate step; None = no SLA


class ProcessRequest(BaseModel):
    """Single-shot: correlate + generate in one call."""
    test_name: str = "ProjectStack Test"
    threads: int = 1
    rampup: int = 1
    loops: int = 1
    use_ai: bool = False
    base_url: str = ""
    include_view_tree: bool = True
    grafana_url: str = ""
    no_transactions: bool = True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "sk-...****"
    return f"sk-...{key[-4:]}"


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in _sessions:
        # Try restoring from disk (survives server restarts)
        restored = _load_session_from_disk(session_id)
        if restored is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return _sessions[session_id]


def _build_timeline(samplers: dict) -> list[dict]:
    """
    Build a synthetic per-second timeline from sampler times.
    Each sampler's response times are distributed evenly across
    simulated seconds proportional to the number of samples.
    """
    # Collect all (time_ms, is_error) pairs spread across seconds
    all_entries: list[dict] = []

    for label, sm in samplers.items():
        total = sm.total
        times = sm.times if sm.times else []
        n = len(times)
        if n == 0:
            continue
        # Assign each sample a synthetic second bucket
        for i, t in enumerate(times):
            second = int(i / n * max(1, total // 10 + 1))
            all_entries.append({
                "second": second,
                "ms": t,
                "is_error": (i >= sm.passed),
            })

    if not all_entries:
        return []

    # Group by second
    by_second: dict[int, dict] = {}
    for e in all_entries:
        s = e["second"]
        if s not in by_second:
            by_second[s] = {"second": s, "requests": 0, "errors": 0, "total_ms": 0}
        by_second[s]["requests"] += 1
        by_second[s]["total_ms"] += e["ms"]
        if e["is_error"]:
            by_second[s]["errors"] += 1

    timeline = []
    for s in sorted(by_second.keys()):
        bucket = by_second[s]
        reqs = bucket["requests"]
        avg = round(bucket["total_ms"] / reqs, 1) if reqs else 0
        timeline.append({
            "second": s,
            "requests": reqs,
            "errors": bucket["errors"],
            "avg_ms": avg,
        })
    return timeline


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/settings")
async def get_settings():
    masked = dict(_settings)
    for k in masked:
        if k in _SECRET_FIELDS and masked[k]:
            masked[k] = _mask_key(masked[k])
    return masked


@app.get("/api/providers")
async def get_providers():
    from projectstack.correlator.llm_router import PROVIDERS
    return PROVIDERS


@app.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    # Non-secret fields — update directly
    for field in ("ai_provider", "ollama_url", "ollama_model",
                  "jmeter_path", "default_threads", "default_rampup", "default_loops",
                  "grafana_url", "gmail_address"):
        val = getattr(body, field, None)
        if val is not None:
            _settings[field] = str(val)

    # API key fields — skip masked placeholder values
    for field in _SECRET_FIELDS:
        val = getattr(body, field, None)
        if val is not None:
            key = str(val)
            if field == "gmail_app_password":
                key = key.replace(" ", "")  # Google shows app passwords in groups of four
            if "..." not in key:          # ignore echoed-back masked values
                _settings[field] = key

    set_jmeter_path(_settings.get("jmeter_path"))
    _persist_settings()
    return {"ok": True}


@app.get("/api/jmeter-info")
async def get_jmeter_info():
    """Which JMeter install runs tests and whether it has the Custom Thread Groups plugin."""
    return jmeter_info()


def _recorded_transaction_names(requests: list) -> dict[str, str]:
    """
    Transaction names stored in the recording itself ({str(index): name}) — the
    proxy recorder tags each HAR entry with "_transaction", so a recording keeps
    its transactions when it is filtered or uploaded again later.
    """
    out: dict[str, str] = {}
    for i, r in enumerate(requests):
        raw = getattr(r, "raw", None)
        name = raw.get("_transaction") if isinstance(raw, dict) else None
        if isinstance(name, str) and name.strip():
            out[str(i)] = name.strip()
    return out


@app.post("/api/upload")
async def upload_har(file: UploadFile = File(...)):
    filename = file.filename or "upload"
    is_xml = filename.lower().endswith(".xml")
    suffix = ".xml" if is_xml else ".har"

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as tmp:
        tmp.write(contents)  # type: ignore[arg-type]
        tmp_path = tmp.name

    # Extract domains before filtering so the user can pick base URL
    try:
        domains = get_domains(tmp_path)
    except Exception:
        domains = []

    try:
        if is_xml:
            requests = parse_recording(tmp_path)
        else:
            requests = parse_har(tmp_path)
    except Exception as exc:
        os.unlink(tmp_path)
        label = "recording.xml" if is_xml else "HAR"
        raise HTTPException(status_code=400, detail=f"Failed to parse {label}: {exc}")

    source_format = "recording_xml" if is_xml else "har"
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "har_path": tmp_path,
        "filename": filename,
        "source_format": source_format,
        "requests": requests,
        "correlated_values": None,
        "jmx_path": None,
        "jmx_content": None,
        "results": None,
        "steps_complete": {"upload": True, "correlate": False, "generate": False, "run": False},
    }
    _save_session(session_id)

    preview = [
        {"index": i, "method": r.method, "url": r.url, "status_code": r.status_code}
        for i, r in enumerate(requests)
    ]

    return {
        "session_id": session_id,
        "filename": filename,
        "request_count": len(requests),
        "requests": preview,
        "source_format": source_format,
        "domains": domains,
        "transaction_names": _recorded_transaction_names(requests),
    }


@app.get("/api/har/{session_id}")
async def download_har(session_id: str):
    """The session's recording as a .har file (e.g. to keep a proxy recording)."""
    session = _get_session(session_id)
    har_path = session.get("har_path")
    if not har_path or not Path(har_path).exists() or session.get("source_format") != "har":
        raise HTTPException(status_code=404, detail="No HAR recording for this session")
    filename = session.get("filename") or "recording.har"
    if not filename.endswith(".har"):
        filename += ".har"
    return FileResponse(har_path, media_type="application/json", filename=filename,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/upload-jmx")
async def upload_jmx(file: UploadFile = File(...)):
    """Accept an existing .jmx file, parse its HTTP samplers, and create a session."""
    filename = file.filename or "upload.jmx"
    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".jmx", delete=False, mode="wb") as tmp:
        tmp.write(contents)  # type: ignore[arg-type]
        tmp_path = tmp.name

    # Parse HTTP samplers from JMX using xml.etree
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(tmp_path)
        root = tree.getroot()
        samplers = root.findall(".//{http://jakarta.apache.org/jmeter}HTTPSamplerProxy") or \
                   root.findall(".//HTTPSamplerProxy")
        if not samplers:
            # Try generic element name search
            samplers = [el for el in root.iter() if el.tag in ("HTTPSamplerProxy",)]
        requests_preview = []
        for i, s in enumerate(samplers):
            def _prop(name: str) -> str:
                el = s.find(f".//stringProp[@name='{name}']")
                return el.text or "" if el is not None and el.text else ""
            method = _prop("HTTPSampler.method") or "GET"
            domain = _prop("HTTPSampler.domain")
            port = _prop("HTTPSampler.port")
            path = _prop("HTTPSampler.path") or "/"
            protocol = _prop("HTTPSampler.protocol") or "https"
            if domain:
                base = f"{protocol}://{domain}"
                if port and port not in ("80", "443", ""):
                    base += f":{port}"
                url = base + (path if path.startswith("/") else "/" + path)
            else:
                url = path
            requests_preview.append({
                "index": i,
                "method": method.upper(),
                "url": url,
                "status_code": 200,
            })
    except Exception as exc:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"Failed to parse JMX: {exc}")

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "har_path": tmp_path,
        "jmx_source_path": tmp_path,
        "filename": filename,
        "source_format": "jmx",
        "requests": [],          # raw parsed requests — empty for JMX uploads
        "requests_preview": requests_preview,
        "correlated_values": None,
        "jmx_path": tmp_path,
        "jmx_content": contents.decode("utf-8", errors="replace") if isinstance(contents, bytes) else str(contents),
        "results": None,
        "steps_complete": {"upload": True, "correlate": False, "generate": True, "run": False},
    }

    return {
        "session_id": session_id,
        "filename": filename,
        "request_count": len(requests_preview),
        "requests": requests_preview,
        "source_format": "jmx",
        "domains": [],
    }


# ---------------------------------------------------------------------------
# Proxy recorder (requires mitmproxy: pip install mitmproxy)
# ---------------------------------------------------------------------------

_proxy_proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
_chromium_proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
_proxy_har_path: Optional[str] = None
_proxy_txn_path: Optional[Path] = None      # transaction markers read by the capture addon
_proxy_status_path: Optional[Path] = None   # live counts written by the capture addon
_PROXY_PORT: int = 8082

# Keep the recording browser quiet: no update checks, sync, safe-browsing pings …
_RECORDING_BROWSER_FLAGS = [
    "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--disable-default-apps", "--no-pings",
    "--disable-domain-reliability", "--disable-client-side-phishing-detection",
    "--disable-features=OptimizationHints,Translate,MediaRouter",
]

# Candidate Chromium binary names, tried in order
_CHROMIUM_BINS = [
    "chromium-browser",
    "chromium",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
]


def _find_chromium() -> Optional[str]:
    """Return the first Chromium/Chrome binary found on PATH, or None."""
    import shutil
    for name in _CHROMIUM_BINS:
        path = shutil.which(name)
        if path:
            return path
    return None


def _install_mitmproxy_cert_to_nss() -> bool:
    """
    Install the mitmproxy CA certificate into the user's Chrome NSS database
    (~/.pki/nssdb) so Chromium trusts it without --ignore-certificate-errors.

    Requires certutil (package: libnss3-tools on Debian/Ubuntu).
    Returns True if the cert is trusted after the call, False otherwise.
    """
    import shutil

    cert_path = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    nss_db    = Path.home() / ".pki" / "nssdb"
    nick      = "mitmproxy-projectstack"

    # Can't do anything without certutil
    if not shutil.which("certutil"):
        return False

    # Can't install without the cert (proxy not started yet)
    if not cert_path.exists():
        return False

    def certutil(*args: str) -> subprocess.CompletedProcess:
        # certutil asks for passwords on stdin; without a terminal it can prompt
        # forever (filling memory with prompts), so it gets no stdin and a time limit
        return subprocess.run(["certutil", "-d", f"sql:{nss_db}", *args],
                              capture_output=True, stdin=subprocess.DEVNULL, timeout=15)

    try:
        # Create the NSS database only if it is missing — running -N on an existing
        # one makes certutil prompt for the current password
        if not ((nss_db / "cert9.db").exists() or (nss_db / "cert8.db").exists()):
            nss_db.mkdir(parents=True, exist_ok=True)
            certutil("-N", "--empty-password")

        if certutil("-L", "-n", nick).returncode == 0:
            return True  # already trusted

        return certutil("-A", "-t", "CT,,", "-n", nick, "-i", str(cert_path)).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


class ProxyStartRequest(BaseModel):
    start_url: Optional[str] = None      # page the recording browser opens first
    transaction: Optional[str] = None    # name of the first transaction
    launch_browser: bool = True          # False: point your own browser at the proxy


def _write_markers(markers: list[dict]) -> None:
    assert _proxy_txn_path is not None
    tmp = _proxy_txn_path.with_name(_proxy_txn_path.name + ".tmp")
    tmp.write_text(json.dumps(markers), encoding="utf-8")
    os.replace(tmp, _proxy_txn_path)


def _read_markers() -> list[dict]:
    try:
        return json.loads(_proxy_txn_path.read_text(encoding="utf-8")) if _proxy_txn_path else []
    except (OSError, ValueError):
        return []


def _clean_txn_input(name: Optional[str]) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="Transaction name is too long (max 100 characters)")
    if name and not clean_transaction_name(name):
        raise HTTPException(status_code=400, detail="Transaction name needs at least one letter or digit")
    return name


@app.post("/api/proxy/start")
async def proxy_start(body: Optional[ProxyStartRequest] = None):
    """Start a local mitmproxy instance and open Chromium pre-configured to use it."""
    global _proxy_proc, _chromium_proc, _proxy_har_path, _proxy_txn_path, _proxy_status_path
    body = body or ProxyStartRequest()

    # If already running, return current state
    if _proxy_proc and _proxy_proc.poll() is None:
        return {"ok": True, "port": _PROXY_PORT, "already_running": True}

    start_url = (body.start_url or "").strip()
    if start_url and not re.match(r"^https?://[^\s]+$", start_url):
        raise HTTPException(status_code=400, detail="Start URL must begin with http:// or https://")
    first_txn = _clean_txn_input(body.transaction)

    work_dir = Path(tempfile.mkdtemp(prefix="ps_recording_"))
    har_path = str(work_dir / "capture.har")
    _proxy_har_path = har_path
    _proxy_txn_path = work_dir / "transactions.json"
    _proxy_status_path = work_dir / "status.json"
    _write_markers([{"ts": time.time(), "name": first_txn}] if first_txn else [])

    addon_script = Path(__file__).resolve().parent.parent / "proxy" / "har_capture.py"
    env = {**os.environ, "PS_HAR_OUTPUT": har_path, "PS_TXN_FILE": str(_proxy_txn_path),
           "PS_STATUS_FILE": str(_proxy_status_path)}

    # Use the mitmdump from the same venv as this process
    _venv_bin = Path(sys.executable).parent
    mitmdump_bin = str(_venv_bin / "mitmdump")
    if not Path(mitmdump_bin).exists():
        mitmdump_bin = "mitmdump"  # fall back to PATH

    try:
        proc = subprocess.Popen(
            [
                mitmdump_bin,
                "-s", str(addon_script),
                "--listen-port", str(_PROXY_PORT),
                "--ssl-insecure",
                "--quiet",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1.0)   # give mitmdump time to bind
        if proc.poll() is not None:
            return {
                "ok": False,
                "error": "mitmdump exited immediately — is mitmproxy installed? (pip install mitmproxy)",
            }
        _proxy_proc = proc

        # Launch Chromium pre-configured to use the proxy.
        # Try to install the mitmproxy CA cert so we don't need --ignore-certificate-errors.
        # mitmdump writes the cert to ~/.mitmproxy/ after first run; give it a moment.
        await asyncio.sleep(0.5)
        cert_trusted = await asyncio.to_thread(_install_mitmproxy_cert_to_nss)

        chromium_bin = _find_chromium() if body.launch_browser else None
        chromium_launched = False
        if chromium_bin:
            profile_dir = str(work_dir / "browser-profile")
            # Only fall back to --ignore-certificate-errors if cert install failed
            extra_flags = [] if cert_trusted else ["--ignore-certificate-errors"]
            try:
                _chromium_proc = subprocess.Popen(
                    [
                        chromium_bin,
                        f"--proxy-server=127.0.0.1:{_PROXY_PORT}",
                        # The proxy must also see localhost traffic (Chrome bypasses it by default)
                        "--proxy-bypass-list=<-loopback>",
                        *extra_flags,
                        f"--user-data-dir={profile_dir}",
                        *_RECORDING_BROWSER_FLAGS,
                        "--new-window",
                        start_url or "about:blank",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                chromium_launched = True
            except Exception:
                pass

        return {
            "ok": True,
            "port": _PROXY_PORT,
            "chromium_launched": chromium_launched,
            "chromium_bin": chromium_bin,
            "cert_trusted": cert_trusted,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "mitmdump not found — install mitmproxy first: pip install mitmproxy",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class ProxyTransactionRequest(BaseModel):
    name: str


@app.post("/api/proxy/transaction")
async def proxy_transaction(body: ProxyTransactionRequest):
    """
    Start a new transaction while recording (like typing a Transaction Controller
    name in JMeter's recorder): requests sent from now on are tagged with it.
    """
    if not (_proxy_proc and _proxy_proc.poll() is None):
        raise HTTPException(status_code=400, detail="The recorder is not running")
    name = _clean_txn_input(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="Enter a transaction name")
    markers = _read_markers()
    markers.append({"ts": time.time(), "name": name})
    _write_markers(markers)
    return await proxy_status()


@app.post("/api/proxy/stop")
async def proxy_stop():
    """Stop the proxy (and Chromium if open), then import captured traffic as a new session."""
    global _proxy_proc, _chromium_proc, _proxy_har_path

    if not _proxy_proc or _proxy_proc.poll() is not None:
        _proxy_proc = None
        return {"ok": False, "error": "No proxy is currently running"}

    def _terminate(p: Optional[subprocess.Popen], timeout: float) -> None:  # type: ignore[type-arg]
        if p is None or p.poll() is not None:
            return
        try:
            p.terminate()
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
        except Exception:
            pass

    # Close Chromium first so it flushes any pending requests (off the event loop)
    await asyncio.to_thread(_terminate, _chromium_proc, 3)
    _chromium_proc = None

    proc = _proxy_proc
    _proxy_proc = None
    await asyncio.to_thread(_terminate, proc, 5)

    if not _proxy_har_path or not Path(_proxy_har_path).exists():
        return {"ok": False, "error": "No captured traffic found — nothing was recorded"}

    # Same filtering as an uploaded HAR: static files and analytics are left out
    try:
        captured = parse_har(_proxy_har_path, filter_noise=True)
    except Exception as exc:
        return {"ok": False, "error": f"Failed to parse captured HAR: {exc}"}

    if not captured:
        return {"ok": False, "error": "Nothing to import — no requests were recorded apart from static files"}

    from datetime import datetime as _dt
    filename = f"recording_{_dt.now().strftime('%Y-%m-%d_%H%M')}.har"
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "har_path": _proxy_har_path,
        "filename": filename,
        "source_format": "har",
        "requests": captured,
        "correlated_values": None,
        "jmx_path": None,
        "jmx_content": None,
        "results": None,
        "steps_complete": {"upload": True, "correlate": False, "generate": False, "run": False},
    }
    _save_session(session_id)
    preview = [
        {"index": i, "method": r.method, "url": r.url, "status_code": r.status_code}
        for i, r in enumerate(captured)
    ]
    return {
        "ok": True,
        "session_id": session_id,
        "filename": filename,
        "request_count": len(captured),
        "requests": preview,
        "source_format": "har",
        "domains": get_domains(_proxy_har_path),
        "transaction_names": _recorded_transaction_names(captured),
    }


@app.get("/api/proxy/status")
async def proxy_status():
    """Running state, live request counts and the transactions recorded so far."""
    running = bool(_proxy_proc and _proxy_proc.poll() is None)
    status: dict = {}
    if running and _proxy_status_path and _proxy_status_path.exists():
        try:
            status = json.loads(_proxy_status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    markers = _read_markers() if running else []
    return {
        "running": running,
        "port": _PROXY_PORT,
        "request_count": status.get("captured", 0),      # everything the browser sent
        "kept_count": status.get("kept", 0),             # what will be imported (no static files)
        "current_transaction": markers[-1]["name"] if markers else "",
        # [{name, request_count}] in recording order, including transactions with no requests yet
        "transactions": _merge_transaction_counts(markers, status.get("transactions", [])),
        "browser_open": bool(_chromium_proc and _chromium_proc.poll() is None),
    }


def _merge_transaction_counts(markers: list[dict], counted: list[list]) -> list[dict]:
    """Transactions the user started (even empty ones) with their request counts."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for name, n in counted:
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + n
    for m in markers:
        if m["name"] not in counts:
            counts[m["name"]] = 0
            order.append(m["name"])
    return [{"name": n, "request_count": counts[n]} for n in order]


@app.post("/api/remove-noise/{session_id}")
async def remove_noise(session_id: str):
    """Identify and return indices of 'noise' requests (static assets, analytics, etc.)."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    requests_preview = session.get("requests_preview") or [
        {"index": i, "method": r.method, "url": r.url, "status_code": r.status_code}
        for i, r in enumerate(requests)
    ]

    noise_patterns = [
        r"\.(?:js|css|png|jpg|jpeg|gif|ico|svg|woff2?|ttf|eot|map|webp|avif)(?:\?|$)",
        r"(?:google-analytics|googletagmanager|facebook\.com/tr|doubleclick|hotjar|mixpanel|segment\.io|amplitude)",
        r"/(?:favicon|robots\.txt|sitemap)",
        r"/__webpack",
    ]
    import re as _re
    noise_indices = []
    reasons: dict[str, str] = {}
    for req in requests_preview:
        url: str = str(req.get("url") or "")
        idx: int = int(req.get("index") or 0)
        for pat in noise_patterns:
            if _re.search(pat, url, _re.IGNORECASE):
                noise_indices.append(idx)
                reasons[str(idx)] = f"Matches noise pattern: {pat}"
                break

    return {"noise_indices": noise_indices, "reasons": reasons}


@app.post("/api/correlate/{session_id}")
async def correlate(session_id: str, body: CorrelateRequest):
    session = _get_session(session_id)
    requests = session["requests"]

    try:
        correlated = await asyncio.to_thread(
            run_correlation, requests, use_ai=body.use_ai, ai_settings=dict(_settings),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Correlation failed: {exc}")

    assessment = assess_correlation(correlated, requests)
    session["correlated_values"] = correlated
    session["correlation_assessment"] = assessment
    session["steps_complete"]["correlate"] = True
    _save_session(session_id)

    values_out = [cv_to_dict(cv) for cv in correlated]
    return {"count": len(correlated), "values": values_out, "assessment": assessment}


def _same_hosts(requests: list, base_urls: list[str]) -> list:
    """Requests whose host is one of the selected base URLs (as /api/filter keeps them)."""
    from urllib.parse import urlparse as _up
    hosts = {u.replace("https://", "").replace("http://", "").rstrip("/") for u in base_urls}
    return [r for r in requests if (_up(r.url).netloc or "") in hosts]


class CorrelateCompareRequest(BaseModel):
    second_session_id: str
    use_ai: bool = False


@app.post("/api/correlate-compare/{session_id}")
async def correlate_compare(session_id: str, body: CorrelateCompareRequest):
    """
    Run correlation on session_id's HAR, enhanced by comparison with a second
    HAR session.  Dynamic values that differ between the two recordings are
    flagged as confirmed_dynamic in the result.
    """
    session1 = _get_session(session_id)
    session2 = _get_session(body.second_session_id)
    requests1 = session1["requests"]
    # The second recording is filtered to the same base URLs as the first
    requests2 = session2["requests"]
    if session1.get("base_urls"):
        requests2 = _same_hosts(requests2, session1["base_urls"])

    try:
        correlated, confirmed_keys, report = await asyncio.to_thread(
            run_correlation_with_comparison,
            requests1,
            requests2,
            use_ai=body.use_ai,
            ai_settings=dict(_settings),
        )
        assessment = await asyncio.to_thread(assess_correlation, correlated, requests1, requests2, report)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Comparison correlation failed: {exc}")

    session1["correlated_values"] = correlated
    session1["correlation_assessment"] = assessment
    session1["second_session_id"] = body.second_session_id
    session1["steps_complete"]["correlate"] = True
    _save_session(session_id)

    values_out = [
        {**cv_to_dict(cv), "extract_value_2": report["values_2"].get(cv.extract_value, "")}
        for cv in correlated
    ]
    return {
        "count": len(correlated), "values": values_out, "assessment": assessment,
        "comparison": {k: v for k, v in report.items() if k != "values_2"},
    }


class AiCorrelateRequest(BaseModel):
    provider: Optional[str] = None   # defaults to the provider chosen in Settings


@app.post("/api/ai-correlate/{session_id}")
async def ai_correlate(session_id: str, body: AiCorrelateRequest):
    """
    AI auto-correlation: ask the configured LLM for dynamic values the session's
    current correlations miss, and add them. Sends request/response summaries
    to the AI provider.
    """
    from projectstack.correlator.llm_router import PROVIDERS
    session = _get_session(session_id)
    requests = session.get("requests") or []
    if not requests:
        raise HTTPException(status_code=400, detail="No requests in session")
    settings = dict(_settings)
    if body.provider:
        if body.provider not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown AI provider: {body.provider}")
        settings["ai_provider"] = body.provider
    existing = session.get("correlated_values") or []
    try:
        added = await asyncio.to_thread(run_ai_correlation, requests, existing, settings)
    except ValueError as exc:          # missing key / unknown provider
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI auto-correlation failed — {exc}")

    merged = list(existing) + added
    session["correlated_values"] = merged
    session["steps_complete"]["correlate"] = True
    # Re-assess with the AI additions (against the second recording, if one was compared)
    second = _sessions.get(session.get("second_session_id") or "") or (
        _load_session_from_disk(session["second_session_id"]) if session.get("second_session_id") else None)
    requests2 = None
    if second:
        requests2 = second["requests"]
        if session.get("base_urls"):
            requests2 = _same_hosts(requests2, session["base_urls"])
    from projectstack.correlator.engine import pair_requests
    report = {"paired": len(pair_requests(requests, requests2))} if requests2 is not None else None
    assessment = await asyncio.to_thread(assess_correlation, merged, requests, requests2, report)
    session["correlation_assessment"] = assessment
    _save_session(session_id)
    return {
        "count": len(merged),
        "values": [cv_to_dict(cv) for cv in merged],
        "added": [cv_to_dict(cv) for cv in added],
        "provider": settings.get("ai_provider"),
        "assessment": assessment,
    }


class SaveCorrelationsRequest(BaseModel):
    values: list[dict]


@app.put("/api/correlations/{session_id}")
async def save_correlations(session_id: str, body: SaveCorrelationsRequest):
    """
    Replace the session's correlations with the list edited in the UI
    (renamed variables, disabled rows removed, custom rules added). The JMX
    generator and replay read correlations from the session, so edits that
    only live in the browser would otherwise be lost.
    """
    session = _get_session(session_id)
    n = len(session.get("requests") or [])
    values = []
    for d in body.values:
        try:
            cv = cv_from_dict(d)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid correlation: {exc}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cv.var_name):
            raise HTTPException(status_code=400, detail=f"Invalid variable name: {cv.var_name!r}")
        if cv.source_request_index >= n:
            raise HTTPException(status_code=400, detail=f"{cv.var_name}: source request out of range")
        cv.used_in_requests = [u for u in cv.used_in_requests if 0 <= u < n]
        values.append(cv)
    names = [cv.var_name for cv in values]
    dupes = sorted({x for x in names if names.count(x) > 1})
    if dupes:
        raise HTTPException(status_code=400, detail=f"Duplicate variable names: {', '.join(dupes)}")
    session["correlated_values"] = values
    session["steps_complete"]["correlate"] = True
    _save_session(session_id)
    return {"count": len(values), "values": [cv_to_dict(cv) for cv in values]}


class ReplayFixRequest(BaseModel):
    max_iterations: int = 5
    selected_request_indices: Optional[list[int]] = None


@app.post("/api/replay-fix/{session_id}")
async def replay_fix(session_id: str, body: ReplayFixRequest):
    """
    Replay the correlated script once with JMeter (1 user, 1 iteration), compare
    every response with the recording, repair extractors, add missed
    correlations and repeat until the script is stable.
    NOTE: this sends real requests to the recorded application.
    """
    from projectstack.runner.replay import replay_and_fix

    session = _get_session(session_id)
    requests = session.get("requests") or []
    if not requests:
        raise HTTPException(status_code=400, detail="No requests in session")
    if not find_jmeter():
        raise HTTPException(status_code=400, detail="JMeter not found — set the JMeter path in Settings")
    correlated = session.get("correlated_values") or []
    work_dir = REPORTS_BASE_DIR / session_id / "replay"
    try:
        result = await asyncio.to_thread(
            replay_and_fix,
            requests,
            correlated,
            body.selected_request_indices,
            max(1, min(body.max_iterations, 8)),
            work_dir,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Replay failed: {exc}")

    session["correlated_values"] = result["correlations"]
    session["replay"] = {k: v for k, v in result.items() if k != "correlations"}
    _save_session(session_id)
    return {
        "values": [cv_to_dict(cv) for cv in result["correlations"]],
        "iterations": result["iterations"],
        "final": result["final"],
        "unverified_changes": result["unverified_changes"],
    }


@app.get("/api/rule-packs")
async def list_rule_packs():
    return {"packs": [
        {"id": p["id"], "name": p.get("name", p["id"]), "description": p.get("description", ""),
         "source": p.get("source", "builtin"), "rule_count": len(p.get("rules", [])), "rules": p.get("rules", [])}
        for p in rule_packs.load_packs()
    ]}


@app.post("/api/rule-packs")
async def upload_rule_pack(pack: dict):
    try:
        rule_packs.save_user_pack(pack)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": pack["id"]}


@app.delete("/api/rule-packs/{pack_id}")
async def delete_rule_pack(pack_id: str):
    if not rule_packs.delete_user_pack(pack_id):
        raise HTTPException(status_code=404, detail="User rule pack not found (built-in packs cannot be deleted)")
    return {"ok": True}


class CorrelationRuleRequest(BaseModel):
    var_name: str
    source_request_index: int
    extract_from: str          # "body" | "header"
    extract_type: str          # "json" | "regex" | "begin_end"
    extract_key: str | None = None      # json: dot-path; header: header name
    extract_pattern: str | None = None  # regex: pattern with capture group
    extract_begin: str | None = None    # begin_end
    extract_end: str | None = None      # begin_end


def _get_nested(obj: Any, dotted_key: str) -> str | None:
    """Walk a nested dict/list using dot-notation or JSONPath ($.field.sub) notation."""
    # Strip leading JSONPath root sigil ($. or just $)
    if dotted_key.startswith("$."):
        dotted_key = dotted_key[2:]
    elif dotted_key.startswith("$"):
        dotted_key = dotted_key[1:]
    if not dotted_key:
        return str(obj) if not isinstance(obj, (dict, list)) else None
    curr = obj
    for part in dotted_key.split("."):
        if isinstance(curr, dict):
            curr = curr.get(part)
        elif isinstance(curr, list) and part.isdigit():
            curr = curr[int(part)]
        else:
            return None
        if curr is None:
            return None
    return str(curr) if not isinstance(curr, (dict, list)) else None


@app.post("/api/apply-rule/{session_id}")
async def apply_correlation_rule(session_id: str, body: CorrelationRuleRequest):
    """
    Apply a user-defined correlation rule: extract a value from a source
    request/response and auto-scan all subsequent requests for usage.
    Returns a CorrelatedValue-shaped dict ready to merge into the correlation table.
    """
    session = _get_session(session_id)
    requests_list = session["requests"]

    if body.source_request_index >= len(requests_list):
        raise HTTPException(status_code=400, detail="Invalid source request index")

    if body.source_request_index < 0:
        raise HTTPException(status_code=400, detail="Invalid source request index")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", body.var_name or ""):
        raise HTTPException(status_code=400, detail="Variable name must be letters, digits and underscores")

    src = requests_list[body.source_request_index]
    extracted_value: str | None = None
    jmx_extract_key = body.extract_key or body.extract_pattern or ""
    detection_pass = "custom_rule_json"
    extractor = ""
    extractor_config: dict = {}

    if body.extract_from == "body":
        response_text = src.response_body or ""
        if body.extract_type == "json" and body.extract_key:
            try:
                data = json.loads(response_text)
                extracted_value = _get_nested(data, body.extract_key)
            except Exception:
                pass
            detection_pass = "custom_rule_json"
            extractor = "json"
            key = body.extract_key
            extractor_config = {"path": key if key.startswith("$") else f"$.{key}"}

        elif body.extract_type == "regex" and body.extract_pattern:
            try:
                m = re.search(body.extract_pattern, response_text)
            except re.error as exc:
                raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}")
            if m:
                extracted_value = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            pattern = body.extract_pattern if m and m.lastindex else f"({body.extract_pattern})"
            jmx_extract_key = pattern
            detection_pass = "custom_rule_regex"
            extractor = "regex"
            extractor_config = {"regex": pattern, "match_no": 1}

        elif body.extract_type == "begin_end":
            begin = body.extract_begin or ""
            end = body.extract_end or ""
            si = response_text.find(begin)
            if si != -1:
                si += len(begin)
                ei = response_text.find(end, si)
                if ei != -1:
                    extracted_value = response_text[si:ei]
            # JMeter's Boundary Extractor takes the boundaries literally — no regex escaping
            jmx_extract_key = f"{begin}…{end}"
            detection_pass = "custom_rule_boundary"
            extractor = "boundary"
            extractor_config = {"lb": begin, "rb": end, "match_no": 1}

    elif body.extract_from == "header":
        header_name = body.extract_key or ""
        extracted_value = next(
            (v for k, v in (src.response_headers or {}).items() if k.lower() == header_name.lower()), None
        )
        jmx_extract_key = header_name
        detection_pass = "custom_rule_header"
        extractor = "header"
        extractor_config = {"name": header_name}

    if not extracted_value:
        raise HTTPException(
            status_code=400,
            detail="Could not extract a value — check your rule configuration and source request",
        )

    # Auto-scan later requests for usage of the extracted value (raw or encoded).
    # Only requests after the source can use it — the value does not exist before.
    uses = [
        k for k in range(body.source_request_index + 1, len(requests_list))
        if find_encoding(request_haystack(requests_list[k]), extracted_value)
    ]

    from projectstack.correlator.engine import CorrelatedValue
    cv = CorrelatedValue(
        var_name=body.var_name,
        source_request_index=body.source_request_index,
        extract_from=body.extract_from,
        extract_key=jmx_extract_key,
        extract_value=extracted_value,
        used_in_requests=uses,
        detection_pass=detection_pass,
        reason="User-defined correlation rule",
        extractor=extractor,
        extractor_config=extractor_config,
    )
    return {**cv_to_dict(cv), "extract_value_2": None}


@app.post("/api/filter/{session_id}")
async def filter_by_base_url(session_id: str, base_url: list[str] = Query(default=[])):
    """Re-parse the uploaded HAR filtering to one or more base URL domains."""
    session = _get_session(session_id)
    har_path = session.get("har_path", "")
    if not har_path or not Path(har_path).exists():
        raise HTTPException(status_code=400, detail="No HAR file found for this session")

    # Deduplicate and strip empty strings
    urls = [u for u in base_url if u.strip()]
    try:
        is_xml = har_path.endswith(".xml")
        if is_xml:
            all_requests = parse_recording(har_path)
            if urls:
                from urllib.parse import urlparse as _up
                def _matches(r: Any) -> bool:
                    try:
                        host = _up(r.url).hostname or ""
                        return any(u.replace("https://", "").replace("http://", "").rstrip("/") == host for u in urls)
                    except Exception:
                        return False
                all_requests = [r for r in all_requests if _matches(r)]
            requests = all_requests
        else:
            if not urls:
                requests = parse_har(har_path, base_url=None)
            elif len(urls) == 1:
                requests = parse_har(har_path, base_url=urls[0])
            else:
                # Parse once per URL and merge while preserving order
                seen_urls: set[str] = set()
                requests = []
                for r in parse_har(har_path, base_url=None):
                    from urllib.parse import urlparse as _up2
                    try:
                        host = _up2(r.url).hostname or ""
                    except Exception:
                        host = ""
                    for u in urls:
                        clean = u.replace("https://", "").replace("http://", "").rstrip("/")
                        if host == clean:
                            key = f"{r.method}:{r.url}"
                            if key not in seen_urls:
                                seen_urls.add(key)
                                requests.append(r)
                            break
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to re-parse: {exc}")

    session["requests"] = requests
    session["base_urls"] = urls
    session["correlated_values"] = None
    session["steps_complete"]["correlate"] = False
    _save_session(session_id)

    preview = [
        {"index": i, "method": r.method, "url": r.url, "status_code": r.status_code}
        for i, r in enumerate(requests)
    ]
    return {"request_count": len(requests), "requests": preview,
            "transaction_names": _recorded_transaction_names(requests)}


@app.get("/api/request-detail/{session_id}/{index}")
async def get_request_detail(session_id: str, index: int):
    """Return full details for a single request including body, response, and associated dynamic values."""
    session = _get_session(session_id)
    requests = session["requests"]
    if index < 0 or index >= len(requests):
        raise HTTPException(status_code=404, detail=f"Request index {index} out of range")
    req = requests[index]
    correlated = session.get("correlated_values") or []
    from urllib.parse import urlparse
    parsed = urlparse(req.url)

    # Dynamic values: used in this request's payload or extracted from its response
    dynamic_values = []
    for cv in correlated:
        if index in (cv.used_in_requests or []):
            dynamic_values.append({
                "var_name": cv.var_name,
                "extract_value": (cv.extract_value or "")[:200],
                "role": "used",
            })
        if cv.source_request_index == index:
            dynamic_values.append({
                "var_name": cv.var_name,
                "extract_value": (cv.extract_value or "")[:200],
                "role": "extracted",
            })

    # Truncate large bodies
    max_body = 8000
    return {
        "index": index,
        "method": req.method,
        "url": req.url,
        "path": parsed.path or "/",
        "status_code": req.status_code,
        "headers": dict(req.headers),
        "body": (req.body or "")[:max_body] if req.body else None,
        "body_mime_type": req.body_mime_type,
        "response_status": req.status_code,
        "response_headers": dict(req.response_headers or {}),
        "response_body": (req.response_body or "")[:max_body] if req.response_body else None,
        "response_mime_type": req.response_mime_type,
        "dynamic_values": dynamic_values,
    }


def _transactions_from_names(order: list[int], names: dict[str, str]):
    """
    Group consecutive requests that share a transaction name; unnamed ones join
    the previous group. A Transaction Controller is a block of requests in
    execution order, so a name used in two places becomes two controllers.
    """
    from projectstack.generator.jmx_generator import Transaction
    groups: list[tuple[str, list[int]]] = []
    for idx in order:
        name = clean_transaction_name(names.get(str(idx)) or "")
        if groups and (not name or name == groups[-1][0]):
            groups[-1][1].append(idx)
        else:
            groups.append((name or "Flow", [idx]))
    return [Transaction(name=f"TC_{n:02d}_{name}", request_indices=idxs)
            for n, (name, idxs) in enumerate(groups, start=1)]


def _ordered_indices(n: int, request_order: Optional[list[int]], selected: Optional[list[int]]) -> list[int]:
    """Requests in execution order: the user's order (if any), limited to the selection (if any)."""
    order = [i for i in (request_order if request_order is not None else range(n)) if 0 <= i < n]
    if selected is not None:
        keep = set(selected)
        order = [i for i in order if i in keep]
    return order


@app.post("/api/generate/{session_id}")
async def generate(session_id: str, body: GenerateRequest):
    session = _get_session(session_id)
    requests = session["requests"]
    correlated = session.get("correlated_values") or []

    # Filter to selected vars only
    selected: list[str] | None = body.selected_var_names
    if selected is not None:
        correlated = [cv for cv in correlated if cv.var_name in selected]

    # Parse custom assertions (JSON keys are strings; convert to int)
    custom_assertions: dict[int, list] = {}
    for k, v in (body.custom_assertions or {}).items():
        try:
            custom_assertions[int(k)] = v
        except (ValueError, TypeError):
            pass

    # Requests in execution order (user order, limited to the selection)
    final_indices: Optional[list[int]]
    if body.request_order is None and body.selected_request_indices is None:
        final_indices = None
    else:
        final_indices = _ordered_indices(len(requests), body.request_order, body.selected_request_indices)
    order = final_indices if final_indices is not None else list(range(len(requests)))

    # Transaction grouping: the user's grouping wins; otherwise group automatically,
    # in the same execution order as the samplers
    if body.no_transactions:
        transactions = None
    elif body.custom_transaction_names and any(v.strip() for v in body.custom_transaction_names.values()):
        transactions = _transactions_from_names(order, body.custom_transaction_names)
    else:
        transactions = _transactions_from_names(order, transaction_names(order, requests))

    tg_config = body.thread_group.model_dump() if body.thread_group else None
    if tg_config and tg_config["type"] == "ultimate" and not tg_config["rows"]:
        raise HTTPException(status_code=400, detail="Ultimate Thread Group needs at least one schedule row")

    grafana_url = body.grafana_url or _settings.get("grafana_url", "")

    # Apply load profile presets (override threads/rampup/loops)
    profile = _LOAD_PROFILES.get(body.load_profile, {})
    eff_threads = profile.get("threads", body.threads)
    eff_rampup  = profile.get("rampup",  body.rampup)
    eff_loops   = -1 if body.infinite_loops else profile.get("loops", body.loops)

    with tempfile.NamedTemporaryFile(suffix=".jmx", delete=False, mode="w") as tmp:
        jmx_path = tmp.name

    try:
        generate_jmx(
            requests=requests,
            correlated_values=correlated,
            output_path=jmx_path,
            test_name=body.test_name,
            threads=eff_threads,
            ramp_up=eff_rampup,
            loops=eff_loops,
            transactions=transactions,
            base_url=body.base_urls[0] if body.base_urls else body.base_url,
            base_urls=body.base_urls or ([body.base_url] if body.base_url else []),
            test_vars=body.test_vars or [],
            test_data_files=body.test_data_files or [],
            include_view_tree=body.include_view_tree,
            grafana_url=grafana_url,
            no_transactions=body.no_transactions,
            selected_indices=final_indices,
            custom_assertions=custom_assertions if custom_assertions else None,
            include_think_times=body.include_think_times,
            duration_seconds=body.duration_seconds,
            thread_group=tg_config,
            correlation_markers=body.correlation_markers,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"JMX generation failed: {exc}")

    try:
        validation_errors = validate_jmx(jmx_path)
    except Exception:
        validation_errors = []

    jmx_content = Path(jmx_path).read_text(encoding="utf-8")
    session["jmx_path"] = jmx_path
    session["jmx_content"] = jmx_content
    session["steps_complete"]["generate"] = True
    _save_session(session_id)

    txn_count = 0 if body.no_transactions else len(transactions or [])
    return {
        "load_config": read_thread_settings(jmx_path),
        "thread_group": read_thread_group(jmx_path),
        "transactions": [{"name": t.name, "request_count": len(t.request_indices)} for t in transactions or []],
        "jmx": jmx_content,
        "length": len(jmx_content),
        "transaction_count": txn_count,
        "sampler_count": len(requests),
        "validation_errors": [
            {"code": e.code, "message": e.message} for e in validation_errors
        ],
    }


@app.get("/api/download/{session_id}")
async def download_jmx(session_id: str):
    session = _get_session(session_id)
    jmx_path = session.get("jmx_path")
    if not jmx_path or not Path(jmx_path).exists():
        raise HTTPException(status_code=404, detail="JMX not yet generated for this session")
    filename = f"{session.get('filename', 'test').replace('.har', '')}_test.jmx"
    return FileResponse(
        path=jmx_path,
        media_type="application/xml",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/run/{session_id}")
async def run_test(session_id: str, body: RunRequest):
    session = _get_session(session_id)
    jmx_path = session.get("jmx_path")
    if not jmx_path or not Path(jmx_path).exists():
        raise HTTPException(status_code=400, detail="Generate JMX first before running")

    try:
        sla = normalize_sla(body.sla.model_dump() if body.sla else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"SLA: {exc}")

    # Stepping / Concurrency / Ultimate thread groups need JMeter Plugins
    plugin_tg = required_plugin(jmx_path)
    if plugin_tg and not jmeter_info()["custom_thread_groups"]:
        info = jmeter_info()
        raise HTTPException(status_code=400, detail=(
            f"This script uses the {plugin_tg.title()} Thread Group from JMeter Plugins, but the JMeter at "
            f"{info['home'] or 'the configured path'} does not have it. Install the 'Custom Thread Groups' "
            "plugin (jpgc-casutg) into lib/ext, or set Settings → JMeter path to an install that has it."
        ))

    # Effective settings: what the generated script says, overridden by the request.
    # Plugin thread groups follow their own schedule, so overrides do not apply.
    effective = {**read_thread_settings(jmx_path)}
    overrides = {key: getattr(body, key) for key in ("threads", "rampup", "loops", "duration_seconds")}
    if plugin_tg:
        overrides = {key: None for key in overrides}
    for key, val in overrides.items():
        if val is not None:
            effective[key] = val
    if (effective.get("threads") or 1) < 1:
        raise HTTPException(status_code=400, detail="Virtual users must be at least 1")
    if effective.get("loops") == -1 and not effective.get("duration_seconds"):
        raise HTTPException(status_code=400, detail="Infinite loops need a duration — set Duration (seconds)")

    # Use a persistent per-session directory so the HTML report survives the request
    run_dir = REPORTS_BASE_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    html_report_dir = run_dir / "html"

    # A duration test must not be killed early: allow the full duration + ramp-up + margin
    duration = effective.get("duration_seconds") or 0
    run_timeout = (duration + (effective.get("rampup") or 0) + 300) if duration else 3600

    if any(lr.status in ("starting", "running", "stopping") for lr in _live_runs.values()):
        raise HTTPException(status_code=409, detail="Another test is already running — wait for it or stop it first")
    # Live monitor: tails this run's results file while JMeter writes it
    (run_dir / "results.jtl").unlink(missing_ok=True)
    expected = None if duration else count_samplers(jmx_path) * (effective.get("threads") or 1) * max(1, effective.get("loops") or 1)
    live = LiveRun(run_dir / "results.jtl", effective, expected)
    _live_runs[session_id] = live

    try:
        # JMeter blocks for the whole test — run it off the event loop so the API
        # (status polling, other pages) stays responsive meanwhile.
        # With a response-time SLA, the JMeter dashboard's Apdex uses it as the
        # "satisfied" threshold (tolerated = 4 × T, as Apdex defines)
        apdex_props = {}
        if sla and sla.get("response_time_ms"):
            t = int(sla["response_time_ms"])
            apdex_props = {"jmeter.reportgenerator.apdex_satisfied_threshold": str(t),
                           "jmeter.reportgenerator.apdex_tolerated_threshold": str(4 * t)}
        run_result = await asyncio.to_thread(
            run_jmeter,
            jmx_path=jmx_path,
            output_dir=str(run_dir),
            threads=overrides["threads"],
            ramp_up=overrides["rampup"],
            loops=overrides["loops"],
            duration_seconds=overrides["duration_seconds"],
            timeout=run_timeout,
            html_report_dir=str(html_report_dir),
            extra_props=apdex_props,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"JMeter not found. Configure path in Settings. Error: {exc}",
        )
    except subprocess.TimeoutExpired:
        live.status, live.error = "failed", f"JMeter did not finish within {run_timeout}s"
        raise HTTPException(status_code=500, detail=live.error)
    except Exception as exc:
        live.status, live.error = "failed", str(exc)
        raise HTTPException(status_code=500, detail=f"JMeter run failed: {exc}")
    live.poll()
    live.status = "done"

    # Results come only from this run's file — never fall back to an older one
    jtl_path = run_result.get("jtl_path") or str(run_dir / "results.jtl")
    if not Path(jtl_path).exists() or Path(jtl_path).stat().st_size == 0:
        tail = ((run_result.get("stdout") or "") + (run_result.get("stderr") or "")).strip()[-600:]
        live.status, live.error = "failed", f"JMeter produced no results. {tail}".strip()
        raise HTTPException(status_code=500, detail=live.error)

    try:
        summary = parse_jtl(jtl_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse results: {exc}")

    timeline = _build_timeline(summary.samplers)

    by_label = {}
    for label, sm in summary.samplers.items():
        by_label[label] = {
            "total": sm.total,
            "passed": sm.passed,
            "failed": sm.failed,
            "error_rate": round(sm.error_rate, 2),
            "avg_ms": round(sm.avg_ms, 1),
            "p90_ms": sm.p90_ms,
            "p95_ms": sm.p95_ms,
            "p99_ms": sm.p99_ms,
        }

    result_payload = {
        "total_requests": summary.total_requests,
        "passed": summary.passed,
        "failed": summary.failed,
        "error_rate": round(summary.error_rate, 2),
        "throughput": round(summary.throughput, 2),
        "avg_ms": round(summary.avg_ms, 1),
        "min_ms": summary.min_ms,
        "max_ms": summary.max_ms,
        "p90_ms": summary.p90_ms,
        "p95_ms": summary.p95_ms,
        "duration_sec": round(summary.duration_sec, 2),
        "by_label": by_label,
        "timeline": timeline,
    }

    result_payload["run_config"] = effective
    result_payload["thread_group"] = read_thread_group(jmx_path)
    result_payload["sla"] = evaluate_sla(sla, by_label, summary.duration_sec)
    session["results"] = result_payload
    session["html_report_dir"] = str(html_report_dir)
    session["steps_complete"]["run"] = True
    _save_session(session_id)

    html_ready = (html_report_dir / "index.html").exists()
    result_payload["html_report_url"] = (
        f"/api/jmeter-reports/{session_id}/html/index.html" if html_ready else None
    )
    return result_payload


@app.get("/api/run-live/{session_id}")
async def run_live(session_id: str):
    """Live metrics of the session's running (or last) test, read from the results file as JMeter writes it."""
    live = _live_runs.get(session_id)
    if live is None:
        raise HTTPException(status_code=404, detail="No test has been started for this session")
    return live.snapshot()


@app.post("/api/run-stop/{session_id}")
async def run_stop(session_id: str, graceful: bool = True):
    """Stop the running test. Graceful stop lets in-flight requests finish and still builds the report."""
    live = _live_runs.get(session_id)
    if live is None or live.status not in ("starting", "running"):
        raise HTTPException(status_code=400, detail="No test is running for this session")
    if not await asyncio.to_thread(stop_jmeter, graceful):
        raise HTTPException(status_code=500, detail="Could not reach JMeter to stop it")
    live.status = "stopping"
    return {"ok": True}


def _session_analysis(session: dict[str, Any]) -> dict[str, Any]:
    """Rule-based insights from the JMeter HTML dashboard, or the JTL summary as a fallback."""
    html_dir = session.get("html_report_dir")
    sla_result = (session.get("results") or {}).get("sla")
    if html_dir and (Path(html_dir) / "statistics.json").exists():
        try:
            return derive_insights(parse_html_report(html_dir), sla_result)
        except Exception as exc:  # malformed report — fall back to the JTL summary
            print(f"  [report] Could not parse HTML report: {exc}")
    results = session.get("results")
    if not results:
        raise HTTPException(status_code=400, detail="No test results found — run a test first")
    analysis = derive_insights(from_session_results(results), results.get("sla"))
    analysis["note"] = "JMeter HTML report not available — insights are based on the summary results only."
    return analysis


_AI_REPORT_PROMPT = (
    "You are a senior performance engineer writing for engineers and managers. Below are JMeter results "
    "and rule-based findings. Write a concise analysis in plain text (no markdown, no headings, no bullets): "
    "one short paragraph on overall health, one paragraph "
    "on the most important problems and their likely causes, then 2-4 specific, prioritised recommendations, "
    "each as its own short paragraph starting with 'Recommendation:'. Refer to transactions by name and quote "
    "numbers. Do not invent data that is not in the input. If the input says no SLA was defined, do not "
    "declare the test passed or failed and do not invent response-time or error targets — describe what was "
    "measured. If an SLA is given, judge the results only against it. Keep it under 280 words."
)


def _ai_summary(analysis: dict[str, Any]) -> str:
    from projectstack.correlator.llm_router import call_llm_prompt
    # The narrative is capped at ~280 words, so a small output budget keeps it fast
    return call_llm_prompt(_AI_REPORT_PROMPT, build_llm_context(analysis), _settings, max_tokens=1024).strip()


@app.get("/api/analyze-report/{session_id}")
async def analyze_report(session_id: str):
    """Use the configured LLM to generate a natural-language summary of the last test run."""
    session = _get_session(session_id)
    analysis = _session_analysis(session)
    try:
        summary = await asyncio.to_thread(_ai_summary, analysis)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI analysis failed: {exc}")
    session.setdefault("insights", analysis)["ai_summary"] = summary
    return {"summary": summary}


@app.get("/api/report-insights/{session_id}")
async def report_insights(session_id: str, ai: bool = Query(False)):
    """High-level insights from the JMeter HTML report; `ai=true` adds an LLM narrative."""
    session = _get_session(session_id)
    analysis = _session_analysis(session)
    if ai:
        try:
            analysis["ai_summary"] = await asyncio.to_thread(_ai_summary, analysis)
        except Exception as exc:
            # The rule-based insights are still useful without the narrative
            analysis["ai_error"] = str(exc)
    elif (session.get("insights") or {}).get("ai_summary"):
        analysis["ai_summary"] = session["insights"]["ai_summary"]
    session["insights"] = analysis
    _save_session(session_id)
    return analysis


def _report_meta(session: dict[str, Any], session_id: str, analysis: dict[str, Any], note: str = "",
                 include_ai: bool = True) -> dict[str, Any]:
    test_name = (session.get("filename") or "Performance test").rsplit(".", 1)[0]
    m = re.search(r'testname="([^"]+)"', session.get("jmx_content") or "")
    if m and m.group(1) not in ("Test Plan", "Thread Group"):
        test_name = m.group(1)
    return {
        "test_name": test_name,
        "filename": session.get("filename"),
        "note": note.strip(),
        "ai_summary": analysis.get("ai_summary") if include_ai else None,
    }


@app.get("/api/insights-report/{session_id}")
async def insights_report(session_id: str):
    """Download the insights report as a standalone HTML file (same content as the email)."""
    session = _get_session(session_id)
    analysis = session.get("insights") or _session_analysis(session)
    meta = _report_meta(session, session_id, analysis)
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", meta["test_name"]) + "_insights.html"
    return HTMLResponse(
        content=email_report.render_report_html(analysis, meta),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _smtp_target() -> dict[str, Any]:
    """PROJECTSTACK_SMTP_DEBUG=host:port sends to a local plain-SMTP sink instead of Gmail (for testing)."""
    debug = os.environ.get("PROJECTSTACK_SMTP_DEBUG", "")
    if debug:
        host, _, port = debug.partition(":")
        return {"host": host or "localhost", "port": int(port or 1025), "use_ssl": False}
    return {}


@app.get("/api/gmail/status")
async def gmail_status():
    return {
        "configured": bool(_settings.get("gmail_address") and _settings.get("gmail_app_password")),
        "address": _settings.get("gmail_address") or None,
    }


@app.post("/api/gmail/verify")
async def gmail_verify():
    """Log in to Gmail with the saved credentials without sending anything."""
    address, password = _settings.get("gmail_address"), _settings.get("gmail_app_password")
    if not address or not password:
        raise HTTPException(status_code=400, detail="Add your Gmail address and app password in Settings first")
    if _smtp_target():
        return {"ok": True, "address": address}
    try:
        await asyncio.to_thread(email_report.verify_gmail, address, password)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "address": address}


@app.post("/api/share-report/{session_id}")
async def share_report(session_id: str, body: ShareReportRequest):
    """Email the insights report (plus optional attachments) through the connected Gmail account."""
    session = _get_session(session_id)
    address, password = _settings.get("gmail_address"), _settings.get("gmail_app_password")
    if not address or not password:
        raise HTTPException(status_code=400, detail="Connect Gmail in Settings before sharing")
    try:
        to = email_report.parse_recipients(body.to)
        cc = email_report.parse_recipients(body.cc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not to:
        raise HTTPException(status_code=400, detail="Add at least one recipient")

    analysis = session.get("insights") or _session_analysis(session)
    meta = _report_meta(session, session_id, analysis, note=body.note, include_ai=body.include_ai)
    verdict = {"pass": "SLA PASS", "at_risk": "AT RISK", "fail": "SLA FAIL"}.get(analysis["verdict"], "")
    subject = body.subject.strip() or (f"[{verdict}] " if verdict else "") + f"Performance test report — {meta['test_name']}"
    html_body = email_report.render_report_html(analysis, meta)
    text_body = email_report.render_report_text(analysis, meta)

    attachments: list[tuple[str, bytes, str, str]] = []
    skipped: list[str] = []
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", meta["test_name"])
    if body.attach_insights_html:
        attachments.append((f"{safe_name}_insights.html", html_body.encode(), "text", "html"))
    html_dir = session.get("html_report_dir")
    if body.attach_jmeter_report:
        if html_dir and (Path(html_dir) / "index.html").exists():
            data = await asyncio.to_thread(email_report.zip_directory, html_dir)
            if len(data) <= email_report.MAX_ATTACHMENT_BYTES:
                attachments.append((f"{safe_name}_jmeter_dashboard.zip", data, "application", "zip"))
            else:
                skipped.append(f"JMeter dashboard ({len(data) // (1024 * 1024)} MB) is over Gmail's attachment limit")
        else:
            skipped.append("JMeter HTML dashboard was not generated for this run")

    msg = email_report.build_message(address, to, cc, subject, html_body, text_body, attachments)
    try:
        await asyncio.to_thread(email_report.send_via_gmail, address, password, msg, **_smtp_target())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "ok": True,
        "from": address,
        "to": to,
        "cc": cc,
        "subject": subject,
        "attachments": [a[0] for a in attachments],
        "skipped": skipped,
    }


@app.get("/api/suggest-params/{session_id}")
async def suggest_params(session_id: str):
    """Return static payload values that are candidates for P_* parameterization."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    correlated = session.get("correlated_values") or []
    from projectstack.generator.jmx_generator import suggest_payload_params
    suggestions = suggest_payload_params(requests, correlated)
    return {"suggestions": suggestions}


@app.get("/api/session/{session_id}")
async def get_session_info(session_id: str):
    session = _get_session(session_id)
    return {
        "session_id": session_id,
        "filename": session.get("filename"),
        "request_count": len(session.get("requests", [])),
        "correlated_count": len(session.get("correlated_values") or []),
        "has_jmx": session.get("jmx_content") is not None,
        "has_results": session.get("results") is not None,
        "steps_complete": session.get("steps_complete", {}),
    }


@app.get("/api/debug/{session_id}")
async def debug_session(session_id: str):
    """Diagnostic endpoint — shows parsed HAR data and per-pass correlation details."""
    session = _get_session(session_id)
    requests = session["requests"]

    # HAR summary
    har_info = []
    for i, r in enumerate(requests):
        har_info.append({
            "index": i,
            "method": r.method,
            "url": r.url,
            "status": r.status_code,
            "has_request_body": bool(r.body),
            "has_response_body": bool(r.response_body),
            "response_body_len": len(r.response_body) if r.response_body else 0,
            "auth_header": r.headers.get("Authorization", "")[:40] or None,
        })

    # Run each pass individually for diagnostics
    from projectstack.correlator.engine import (
        _pass1_jwt_bearer, _pass2_json_keys, _pass3_headers_cookies,
        _pass4_plain_text, _pass5_backward_inference,
    )

    p1 = _pass1_jwt_bearer(requests)
    p2 = _pass2_json_keys(requests)
    p3 = _pass3_headers_cookies(requests)
    p4 = _pass4_plain_text(requests)

    all_candidates = p1 + p2 + p3 + p4
    _pass5_backward_inference(requests, all_candidates)

    def cv_summary(cv_list):  # type: ignore[no-untyped-def]
        return [
            {
                "var_name": cv.var_name,
                "pass": cv.detection_pass,
                "extract_key": cv.extract_key,
                "extract_value": (cv.extract_value or "")[:60],
                "used_in_requests": cv.used_in_requests,
                "actionable": bool(cv.used_in_requests),
            }
            for cv in cv_list
        ]

    provider = _settings.get("ai_provider", "groq")
    key_field_map = {
        "groq": "groq_api_key", "gemini": "gemini_api_key",
        "claude": "anthropic_api_key", "openai": "openai_api_key",
    }
    key_field = key_field_map.get(provider)
    return {
        "settings": {
            "ai_provider": provider,
            "provider_key_set": bool(_settings.get(key_field)) if key_field else True,
        },
        "har_summary": har_info,
        "pass1_results": cv_summary(p1),
        "pass2_results": cv_summary(p2),
        "pass3_results": cv_summary(p3),
        "pass4_results": cv_summary(p4),
        "total_before_filter": len(all_candidates),
        "total_actionable": sum(1 for cv in all_candidates if cv.used_in_requests),
    }


# ---------------------------------------------------------------------------
# /api/process — single-shot: correlate + generate for an existing session
# ---------------------------------------------------------------------------

@app.post("/api/process/{session_id}")
async def process(session_id: str, body: ProcessRequest):
    """Run the full pipeline (correlate → generate → validate) in one call."""
    session = _get_session(session_id)
    requests = session["requests"]

    try:
        correlated = run_correlation(requests, use_ai=body.use_ai, ai_settings=dict(_settings))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Correlation failed: {exc}")

    session["correlated_values"] = correlated
    session["steps_complete"]["correlate"] = True

    order = list(range(len(requests)))
    transactions = None if body.no_transactions else _transactions_from_names(order, transaction_names(order, requests))
    grafana_url = body.grafana_url or _settings.get("grafana_url", "")

    with tempfile.NamedTemporaryFile(suffix=".jmx", delete=False, mode="w") as tmp:
        jmx_path = tmp.name

    try:
        generate_jmx(
            requests=requests,
            correlated_values=correlated,
            output_path=jmx_path,
            test_name=body.test_name,
            threads=body.threads,
            ramp_up=body.rampup,
            loops=body.loops,
            transactions=transactions,
            base_url=body.base_url,
            include_view_tree=body.include_view_tree,
            grafana_url=grafana_url,
            no_transactions=body.no_transactions,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"JMX generation failed: {exc}")

    try:
        validation_errors = validate_jmx(jmx_path)
    except Exception:
        validation_errors = []

    jmx_content = Path(jmx_path).read_text(encoding="utf-8")
    session["jmx_path"] = jmx_path
    session["jmx_content"] = jmx_content
    session["steps_complete"]["generate"] = True

    _save_session(session_id)
    values_out = [cv_to_dict(cv) for cv in correlated]

    return {
        "session_id": session_id,
        "correlated_count": len(correlated),
        "correlated_values": values_out,
        "transaction_count": len(transactions) if transactions else 0,
        "jmx": jmx_content,
        "length": len(jmx_content),
        "validation_errors": [{"code": e.code, "message": e.message} for e in validation_errors],
    }


@app.post("/api/process-file")
async def process_file(
    file: UploadFile = File(...),
    test_name: str = "ProjectStack Test",
    threads: int = 10,
    rampup: int = 30,
    loops: int = 1,
    use_ai: bool = False,
    base_url: str = "",
    include_view_tree: bool = True,
    no_transactions: bool = True,
):
    """Upload a HAR file and immediately run the full pipeline."""
    filename = file.filename or "upload"
    is_xml = filename.lower().endswith(".xml")
    suffix = ".xml" if is_xml else ".har"

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as tmp:
        tmp.write(contents)  # type: ignore[arg-type]
        tmp_path = tmp.name

    try:
        if is_xml:
            requests = parse_recording(tmp_path)
        else:
            requests = parse_har(tmp_path, base_url=base_url or None)
    except Exception as exc:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"Failed to parse HAR: {exc}")

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "har_path": tmp_path,
        "filename": filename,
        "source_format": "har",
        "requests": requests,
        "correlated_values": None,
        "jmx_path": None,
        "jmx_content": None,
        "results": None,
        "steps_complete": {"upload": True, "correlate": False, "generate": False, "run": False},
    }

    body = ProcessRequest(
        test_name=test_name, threads=threads, rampup=rampup, loops=loops,
        use_ai=use_ai, base_url=base_url, include_view_tree=include_view_tree,
        no_transactions=no_transactions,
    )
    result = await process(session_id, body)
    result["request_count"] = len(requests)  # type: ignore[index]
    return result


# ---------------------------------------------------------------------------
# JMeter HTML Report status
# ---------------------------------------------------------------------------

@app.get("/api/report-status/{session_id}")
async def get_report_status(session_id: str):
    """Check whether the JMeter HTML report is available for a session."""
    session = _get_session(session_id)
    html_dir = session.get("html_report_dir")
    ready = bool(html_dir and (Path(html_dir) / "index.html").exists())
    return {
        "available": ready,
        "url": f"/api/jmeter-reports/{session_id}/html/index.html" if ready else None,
    }


# ---------------------------------------------------------------------------
# Grafana Dashboard
# ---------------------------------------------------------------------------

def _build_grafana_dashboard(test_name: str) -> dict:
    """Return a Grafana dashboard JSON for JMeter metrics streamed via InfluxDB."""
    def _ts_panel(panel_id: int, title: str, unit: str, x: int, y: int, w: int, h: int,
                  select_field: str, alias: str, stat: str = "mean",
                  tag_filter: list | None = None) -> dict:
        tags = [
            {"key": "application", "operator": "=~", "value": "/^$application$/"},
            {"condition": "AND", "key": "statut", "operator": "=", "value": "all"},
            {"condition": "AND", "key": "transaction", "operator": "!=", "value": "internal"},
        ]
        if tag_filter:
            tags = tag_filter
        return {
            "datasource": {"type": "influxdb", "uid": "${DS_INFLUXDB}"},
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {"lineWidth": 2},
                    "unit": unit,
                },
            },
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "id": panel_id,
            "options": {
                "legend": {"calcs": ["mean", "max"], "displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "multi"},
            },
            "targets": [{
                "alias": alias,
                "groupBy": [
                    {"params": ["$__interval"], "type": "time"},
                    {"params": ["transaction"], "type": "tag"},
                    {"params": ["null"], "type": "fill"},
                ],
                "measurement": "jmeter",
                "orderByTime": "ASC",
                "policy": "default",
                "refId": "A",
                "resultFormat": "time_series",
                "select": [[{"params": [select_field], "type": "field"}, {"params": [], "type": stat}]],
                "tags": tags,
            }],
            "title": title,
            "type": "timeseries",
        }

    vusers_panel = {
        "datasource": {"type": "influxdb", "uid": "${DS_INFLUXDB}"},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "fixed", "fixedColor": "blue"},
                "custom": {"lineWidth": 2},
                "unit": "short",
            },
        },
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 9},
        "id": 5,
        "options": {"legend": {"displayMode": "list"}, "tooltip": {"mode": "single"}},
        "targets": [{
            "alias": "Active Threads",
            "groupBy": [
                {"params": ["$__interval"], "type": "time"},
                {"params": ["null"], "type": "fill"},
            ],
            "measurement": "jmeter",
            "orderByTime": "ASC",
            "policy": "default",
            "refId": "A",
            "resultFormat": "time_series",
            "select": [[{"params": ["maxAT"], "type": "field"}, {"params": [], "type": "mean"}]],
            "tags": [
                {"key": "application", "operator": "=~", "value": "/^$application$/"},
                {"condition": "AND", "key": "transaction", "operator": "=", "value": "internal"},
            ],
        }],
        "title": "Virtual Users (Active Threads)",
        "type": "timeseries",
    }

    return {
        "__inputs": [{"name": "DS_INFLUXDB", "label": "InfluxDB", "type": "datasource", "pluginId": "influxdb"}],
        "__requires": [
            {"type": "datasource", "id": "influxdb", "name": "InfluxDB", "version": "1.0.0"},
            {"type": "panel", "id": "timeseries", "name": "Time series", "version": ""},
        ],
        "annotations": {"list": []},
        "description": f"JMeter Performance Dashboard — {test_name}",
        "editable": True,
        "graphTooltip": 1,
        "id": None,
        "panels": [
            _ts_panel(1, "Response Time — Avg & P90", "ms", 0, 0, 12, 8,
                      "avg", "$tag_transaction"),
            _ts_panel(2, "Response Time — P90", "ms", 12, 0, 12, 8,
                      "pct90.0", "$tag_transaction"),
            _ts_panel(3, "Throughput (req/s)", "reqps", 0, 9, 12, 8,
                      "count", "$tag_transaction", stat="sum"),
            _ts_panel(4, "Error Count", "short", 0, 18, 12, 8,
                      "countError", "$tag_transaction", stat="sum",
                      tag_filter=[
                          {"key": "application", "operator": "=~", "value": "/^$application$/"},
                          {"condition": "AND", "key": "transaction", "operator": "!=", "value": "internal"},
                      ]),
            vusers_panel,
        ],
        "refresh": "5s",
        "schemaVersion": 38,
        "tags": ["jmeter", "performance", "projectstack"],
        "templating": {
            "list": [{
                "current": {},
                "datasource": {"type": "influxdb", "uid": "${DS_INFLUXDB}"},
                "definition": "SHOW TAG VALUES FROM jmeter WITH KEY = application",
                "hide": 0,
                "includeAll": False,
                "name": "application",
                "options": [],
                "query": "SHOW TAG VALUES FROM jmeter WITH KEY = application",
                "refresh": 2,
                "sort": 0,
                "type": "query",
                "current": {"selected": False, "text": test_name, "value": test_name},
            }],
        },
        "time": {"from": "now-15m", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": f"JMeter — {test_name}",
        "uid": "jmeter-ps-" + re.sub(r"[^a-z0-9]", "", test_name.lower())[:8],
        "version": 1,
    }


@app.get("/api/grafana-dashboard/{session_id}")
async def get_grafana_dashboard(session_id: str):
    """Return a Grafana dashboard JSON the user can import to monitor the test."""
    session = _get_session(session_id)
    jmx_content = session.get("jmx_content")
    # Try to extract test name from JMX, fall back to session filename
    test_name = session.get("filename", "ProjectStack Test").replace(".har", "")
    if jmx_content:
        m = re.search(r'testname="([^"]+)"', jmx_content)
        if m:
            test_name = m.group(1)
    dashboard = _build_grafana_dashboard(test_name)
    filename = re.sub(r"[^a-zA-Z0-9_\-]", "_", test_name) + "_grafana_dashboard.json"
    return JSONResponse(
        content=dashboard,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Custom HTML Report (summary)
# ---------------------------------------------------------------------------

def _generate_html_report(session: dict) -> str:
    """Build a self-contained HTML report from test results stored in a session."""
    results = session.get("results")
    filename = session.get("filename", "test")
    if not results:
        return "<html><body><h1>No results available</h1></body></html>"

    pass_rate = (
        round(results["passed"] / results["total_requests"] * 100, 1)
        if results["total_requests"] else 0
    )
    error_rate = round(results["error_rate"], 2)

    rows = ""
    for label, m in results.get("by_label", {}).items():
        total = m["total"]
        ep = round(m["passed"] / total * 100, 1) if total else 0
        err_color = "#f85149" if m["error_rate"] > 0 else "#3fb950"
        rows += (
            f"<tr><td>{label}</td><td>{total}</td>"
            f"<td>{ep}%</td>"
            f"<td style='color:{err_color}'>{m['error_rate']:.1f}%</td>"
            f"<td>{m['avg_ms']:.0f}</td><td>{m['p95_ms']}</td></tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ProjectStack Report — {filename}</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:32px}}
  h1{{font-size:24px;margin-bottom:4px}}
  .sub{{color:#8b949e;font-size:14px;margin-bottom:32px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:32px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px}}
  .card .label{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8b949e;margin-bottom:6px}}
  .card .value{{font-size:26px;font-weight:700}}
  .card .sub2{{font-size:12px;color:#8b949e;margin-top:4px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;padding:10px 12px;color:#8b949e;font-weight:500;border-bottom:1px solid #30363d}}
  td{{padding:9px 12px;border-bottom:1px solid #21262d}}
  .section{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-bottom:24px}}
  .section h2{{font-size:16px;margin-bottom:16px;color:#8b949e}}
  .ok{{color:#3fb950}} .warn{{color:#d29922}} .fail{{color:#f85149}}
</style>
</head>
<body>
<h1>Performance Test Report</h1>
<div class="sub">File: {filename} &nbsp;|&nbsp; Generated by ProjectStack</div>

<div class="grid">
  <div class="card"><div class="label">Total Requests</div><div class="value">{results['total_requests']}</div></div>
  <div class="card"><div class="label">Pass Rate</div>
    <div class="value {'ok' if pass_rate>=95 else 'warn' if pass_rate>=80 else 'fail'}">{pass_rate}%</div>
    <div class="sub2">{results['passed']} passed / {results['failed']} failed</div></div>
  <div class="card"><div class="label">Error Rate</div>
    <div class="value {'ok' if error_rate==0 else 'fail'}">{error_rate}%</div></div>
  <div class="card"><div class="label">Throughput</div>
    <div class="value">{results['throughput']:.1f}</div><div class="sub2">req/sec</div></div>
  <div class="card"><div class="label">Avg Response</div>
    <div class="value">{results['avg_ms']:.0f}</div><div class="sub2">ms</div></div>
  <div class="card"><div class="label">P95 Response</div>
    <div class="value">{results['p95_ms']}</div><div class="sub2">ms</div></div>
  <div class="card"><div class="label">Duration</div>
    <div class="value">{results['duration_sec']:.1f}</div><div class="sub2">seconds</div></div>
  <div class="card"><div class="label">Min / Max</div>
    <div class="value" style="font-size:18px">{results['min_ms']} / {results['max_ms']}</div>
    <div class="sub2">ms</div></div>
</div>

<div class="section">
  <h2>Per-Endpoint Breakdown</h2>
  <table>
    <thead><tr>
      <th>Endpoint</th><th>Total</th><th>Pass %</th><th>Error %</th><th>Avg ms</th><th>P95 ms</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</body>
</html>"""


@app.get("/api/report/{session_id}")
async def get_report(session_id: str):
    """Return a self-contained HTML report for the session's test results."""
    session = _get_session(session_id)
    if not session.get("results"):
        raise HTTPException(status_code=404, detail="No test results yet — run a test first")
    html = _generate_html_report(session)
    filename = (session.get("filename") or "test").replace(".har", "") + "_report.html"
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# HAR analysis
# ---------------------------------------------------------------------------

@app.get("/api/har-debug/{session_id}")
async def har_debug(session_id: str, limit: int = Query(5)):
    """Return raw HAR response.content fields to diagnose missing bodies."""
    session = _get_session(session_id)
    har_path = session.get("har_path")
    if not har_path or not Path(har_path).exists():
        raise HTTPException(status_code=404, detail="HAR file not found")
    import json as _json
    data = _json.loads(Path(har_path).read_text(encoding="utf-8", errors="replace"))
    entries = data.get("log", {}).get("entries", [])[:limit]
    rows = []
    for i, e in enumerate(entries):
        rc = e.get("response", {}).get("content", {})
        rows.append({
            "index": i,
            "url": e.get("request", {}).get("url", "")[:120],
            "status": e.get("response", {}).get("status"),
            "content_keys": list(rc.keys()),
            "mime": rc.get("mimeType"),
            "size": rc.get("size"),
            "encoding": rc.get("encoding"),
            "text_length": len(rc.get("text") or ""),
            "text_sample": (rc.get("text") or "")[:120],
        })
    return {"entries": rows}


@app.get("/api/responses/{session_id}")
async def get_responses(session_id: str, preview_chars: int = Query(500)):
    """Return a lightweight summary of every request's recorded response."""
    session = _get_session(session_id)
    requests_list: list = session.get("requests") or []
    if not requests_list:
        raise HTTPException(status_code=404, detail="No requests in session")

    from urllib.parse import urlparse as _urlparse
    rows = []
    for i, req in enumerate(requests_list):
        body: str | None = req.response_body
        rows.append({
            "index": i,
            "method": req.method,
            "url": req.url,
            "path": _urlparse(req.url).path or req.url,
            "status_code": req.status_code,
            "response_mime_type": req.response_mime_type,
            "response_body_preview": body[:preview_chars] if body else None,
            "response_body_length": len(body) if body else 0,
            "response_headers": dict(req.response_headers) if req.response_headers else {},
        })
    return {"total": len(rows), "responses": rows}


@app.get("/api/har-analysis/{session_id}")
async def har_analysis(session_id: str):
    """Return a full analysis summary of the uploaded HAR (protocols, auth, duplicates, rate limits)."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    if not requests:
        raise HTTPException(status_code=404, detail="No requests in session")
    return analyze_har(requests)


@app.get("/api/scan-sensitive/{session_id}")
async def scan_sensitive(session_id: str):
    """Scan request/response bodies for PII and sensitive tokens."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    if not requests:
        raise HTTPException(status_code=404, detail="No requests in session")
    findings = scan_sensitive_data(requests)
    return {"findings": findings, "total": len(findings)}


@app.get("/api/duplicates/{session_id}")
async def get_duplicates(session_id: str):
    """Return list of duplicate request indices."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    dups = detect_duplicates(requests)
    return {"duplicates": dups, "total": len(dups)}


@app.get("/api/load-profiles")
async def get_load_profiles():
    """Return the available load profile presets."""
    return {"profiles": _LOAD_PROFILES}


# ---------------------------------------------------------------------------
# Export: k6
# ---------------------------------------------------------------------------

@app.get("/api/export-k6/{session_id}")
async def export_k6_script(session_id: str,
                            vus: int = Query(1, ge=1),
                            duration: str = Query("30s")):
    """Generate and return a k6 JavaScript test script for the session."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    correlated = session.get("correlated_values") or []
    if not requests:
        raise HTTPException(status_code=404, detail="No requests in session")

    base_url = session.get("base_url") or ""
    selected: list[int] | None = None
    sel_ids = session.get("selected_request_indices")
    if sel_ids is not None:
        selected = list(sel_ids)

    script = export_k6(
        requests=requests,
        correlated_values=correlated,
        test_name=session.get("filename", "test").replace(".har", ""),
        base_url=base_url,
        vus=vus,
        duration=duration,
        selected_indices=selected,
    )
    filename = (session.get("filename") or "test").replace(".har", "") + "_k6.js"
    return StreamingResponse(
        io.BytesIO(script.encode()),
        media_type="application/javascript",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Export: CI/CD YAML
# ---------------------------------------------------------------------------

@app.get("/api/export-ci/{session_id}")
async def export_ci(session_id: str,
                    platform: str = Query("github", pattern="^(github|jenkins)$")):
    """Generate a GitHub Actions workflow or Jenkins Declarative Pipeline."""
    session = _get_session(session_id)
    test_name = (session.get("filename") or "test").replace(".har", "").replace("_", " ").title()
    jmx_filename = (session.get("filename") or "test").replace(".har", "") + ".jmx"

    # Derive thread config from last generate call if available
    threads = 1
    rampup  = 10
    loops   = 1

    if platform == "github":
        content = export_github_actions(test_name=test_name, jmx_filename=jmx_filename,
                                        threads=threads, rampup=rampup, loops=loops)
        filename = "jmeter-performance-test.yml"
        media_type = "application/x-yaml"
    else:
        content = export_jenkins(test_name=test_name, jmx_filename=jmx_filename,
                                 threads=threads, rampup=rampup, loops=loops)
        filename = "Jenkinsfile"
        media_type = "text/plain"

    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Export: Team ZIP pack (JMX + correlations.json + k6.js + README)
# ---------------------------------------------------------------------------

@app.get("/api/export-zip/{session_id}")
async def export_zip(session_id: str):
    """Return a ZIP containing the JMX, correlations JSON, k6 script, and README."""
    session = _get_session(session_id)
    requests = session.get("requests") or []
    correlated = session.get("correlated_values") or []
    jmx_content = session.get("jmx_content") or ""
    base_name = (session.get("filename") or "test").replace(".har", "")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # JMX script
        if jmx_content:
            zf.writestr(f"{base_name}.jmx", jmx_content)

        # Correlations JSON
        corr_data = [cv_to_dict(cv) for cv in correlated]
        zf.writestr("correlations.json", json.dumps(corr_data, indent=2))

        # k6 script
        if requests:
            k6_script = export_k6(
                requests=requests,
                correlated_values=correlated,
                test_name=base_name,
                base_url=session.get("base_url") or "",
            )
            zf.writestr(f"{base_name}_k6.js", k6_script)

        # GitHub Actions YAML
        ci_yaml = export_github_actions(
            test_name=base_name.replace("_", " ").title(),
            jmx_filename=f"{base_name}.jmx",
        )
        zf.writestr(".github/workflows/jmeter-test.yml", ci_yaml)

        # README
        readme = f"""# {base_name} — ProjectStack Export

## Contents
- `{base_name}.jmx` — JMeter test plan ({len(correlated)} correlated variables)
- `{base_name}_k6.js` — k6 equivalent test script
- `correlations.json` — Extracted dynamic variable definitions
- `.github/workflows/jmeter-test.yml` — GitHub Actions CI pipeline

## Run JMeter
```bash
jmeter -n -t {base_name}.jmx -l results.jtl -e -o results/html
```

## Run k6
```bash
k6 run {base_name}_k6.js
```

## Correlated Variables ({len(correlated)})
{chr(10).join(f"- `{cv.var_name}` — extracted from request #{cv.source_request_index + 1} ({cv.extract_from})" for cv in correlated)}

Generated by ProjectStack.
"""
        zf.writestr("README.md", readme)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{base_name}_projectstack.zip"'},
    )


# ---------------------------------------------------------------------------
# AI: Sort requests into transaction groups
# ---------------------------------------------------------------------------

class TransactionScope(BaseModel):
    """Which requests to group, in execution order (defaults: all, recorded order)."""
    request_order: Optional[list[int]] = None
    selected_request_indices: Optional[list[int]] = None


def _complete_names(order: list[int], names: dict[str, str]) -> dict[str, str]:
    """Clean names and give unnamed requests the name of the group before them (as generation does)."""
    out: dict[str, str] = {}
    prev = ""
    for idx in order:
        name = clean_transaction_name(names.get(str(idx)) or "") or prev
        if name:
            out[str(idx)] = name
            prev = name
    return out


@app.post("/api/auto-transactions/{session_id}")
async def auto_transactions(session_id: str, body: Optional[TransactionScope] = None):
    """Rule-based grouping into transactions (time gaps, URL modules, action keywords) — no AI."""
    session = _get_session(session_id)
    requests_list = session.get("requests") or []
    if not requests_list:
        raise HTTPException(status_code=400, detail="No requests in session")
    scope = body or TransactionScope()
    order = _ordered_indices(len(requests_list), scope.request_order, scope.selected_request_indices)
    names = transaction_names(order, requests_list)
    return {"transaction_names": names, "group_count": len(_transactions_from_names(order, names))}


@app.post("/api/sort-transactions/{session_id}")
async def sort_transactions(session_id: str, body: Optional[TransactionScope] = None):
    """Use the configured LLM to group requests into named JMeter transaction controllers."""
    session = _get_session(session_id)
    requests_list = session.get("requests", [])
    if not requests_list:
        raise HTTPException(status_code=400, detail="No requests in session")
    scope = body or TransactionScope()
    order = _ordered_indices(len(requests_list), scope.request_order, scope.selected_request_indices)
    if not order:
        raise HTTPException(status_code=400, detail="No requests selected")

    provider = _settings.get("ai_provider", "groq")

    # Check that the provider has its API key configured
    from projectstack.correlator.llm_router import PROVIDERS as _PROVIDERS
    prov_info = _PROVIDERS.get(provider, {})
    key_field = prov_info.get("key_field")
    if key_field and not _settings.get(key_field, ""):
        raise HTTPException(
            status_code=400,
            detail=(
                f"No API key configured for {prov_info.get('label', provider)}. "
                f"Go to Settings and enter your {prov_info.get('label', provider)} API key."
            ),
        )

    # Build a rich request summary for better LLM grouping
    from urllib.parse import urlparse as _urlparse
    lines: list[str] = []
    for i in order:
        req = requests_list[i]
        path = _urlparse(req.url).path
        body_preview = ""
        if req.body:
            body_preview = f" | body: {req.body[:80]}"
        lines.append(f"{i}: {req.method} {path} [{req.status_code}]{body_preview}")

    request_summary = "\n".join(lines[:300])  # cap to avoid huge prompts

    system_prompt = (
        "You are a JMeter performance test expert. Given a list of HTTP requests from a HAR "
        "recording, group them into logical JMeter Transaction Controllers that represent user flows. "
        "Name each transaction clearly after the user action (e.g. Login, SearchProduct, AddToCart). "
        "Requests run in the order listed, and each transaction must be a consecutive run of them. "
        "Return ONLY a JSON object where keys are transaction names and values are arrays of "
        "request indices (integers). Every index must appear exactly once. "
        "Base groupings on URL paths, HTTP methods, and body content — infer the user action."
    )
    user_prompt = (
        f"Group these HTTP requests into JMeter transaction controllers.\n\n"
        f"Format: <index>: <METHOD> <path> [<status>] | body: <preview>\n\n"
        f"{request_summary}\n\n"
        f"Return ONLY valid JSON, no explanation."
    )

    try:
        from projectstack.correlator.llm_router import call_llm_prompt
        raw = call_llm_prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=_settings,
        )

        # Extract JSON from LLM response
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            raise ValueError("No JSON found in LLM response")
        groups: dict[str, list[int]] = json.loads(json_match.group(0))

        # Build transactionNames: { "req_idx_str": "name" } — only for requests in scope;
        # any the model left out join the transaction before them
        allowed = set(order)
        raw_names: dict[str, str] = {}
        for tc_name, indices in groups.items():
            for idx in indices if isinstance(indices, list) else []:
                if isinstance(idx, int) and idx in allowed:
                    raw_names[str(idx)] = str(tc_name)
        if not raw_names:
            raise ValueError("The AI returned no usable grouping")
        names = _complete_names(order, raw_names)
        return {"transaction_names": names, "group_count": len(_transactions_from_names(order, names))}

    except Exception as e:
        msg = str(e)
        # Extract the first sentence / up to first URL for quota/auth errors
        clean = msg.split(". ")[0].split("http")[0].strip().rstrip(".")
        if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
            clean = f"Rate limit hit for {provider.title()} — try again later or switch provider in Settings."
        elif "401" in msg or "api key" in msg.lower() or "authentication" in msg.lower():
            clean = f"Invalid API key for {provider.title()} — check Settings."
        raise HTTPException(status_code=500, detail=clean or msg[:200])


# ---------------------------------------------------------------------------
# Raw HAR → JMX conversion (no correlation, no parameterisation)
# ---------------------------------------------------------------------------

@app.post("/api/raw-convert")
async def raw_convert(
    file: UploadFile = File(...),
    test_name: str = Query("Raw Test", description="JMeter test plan name"),
    threads: int = Query(1),
    rampup: int = Query(1),
    loops: int = Query(1),
):
    """Convert a HAR file directly to a JMX script with no correlation or parameterisation.

    All requests are included as-is with their original headers and payloads.
    """
    filename = file.filename or "upload.har"
    is_xml = filename.lower().endswith(".xml")
    suffix = ".xml" if is_xml else ".har"

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as tmp:
        tmp.write(contents)  # type: ignore[arg-type]
        tmp_path = tmp.name

    try:
        requests_list = parse_recording(tmp_path) if is_xml else parse_har(tmp_path)
    except Exception as exc:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {exc}")

    with tempfile.NamedTemporaryFile(suffix=".jmx", delete=False, mode="w") as out:
        jmx_path = out.name

    try:
        generate_jmx(
            requests=requests_list,
            correlated_values=[],
            output_path=jmx_path,
            test_name=test_name,
            threads=threads,
            ramp_up=rampup,
            loops=loops,
            no_transactions=True,
            include_view_tree=False,
            parameterize_payload=False,
            include_assertions=False,
        )
    except Exception as exc:
        os.unlink(tmp_path)
        os.unlink(jmx_path)
        raise HTTPException(status_code=500, detail=f"JMX generation failed: {exc}")

    os.unlink(tmp_path)

    base_name = Path(filename).stem
    jmx_filename = f"{base_name}_raw.jmx"
    return FileResponse(
        jmx_path,
        media_type="application/xml",
        filename=jmx_filename,
        headers={"Content-Disposition": f'attachment; filename="{jmx_filename}"'},
        background=None,
    )


# ---------------------------------------------------------------------------
# Serve built React frontend (production)
# ---------------------------------------------------------------------------

_static = Path(__file__).parent.parent / "ui-web" / "dist"
if _static.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")
