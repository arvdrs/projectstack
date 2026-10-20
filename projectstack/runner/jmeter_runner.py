"""
jMeter runner.

Finds jMeter on the system, optionally overrides ThreadGroup settings
in the JMX, and executes the test in non-GUI mode, returning the path
to the generated JTL results file.
"""

import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


# Common install locations to search if `jmeter` is not on PATH
_SEARCH_PATHS = [
    "/usr/bin/jmeter",
    "/usr/local/bin/jmeter",
    "/opt/jmeter/bin/jmeter",
    "/opt/apache-jmeter/bin/jmeter",
]

# JMeter install chosen in Settings (executable, bin/ directory or install root)
_configured_path: str | None = None

# Thread groups that need the JMeter Plugins "Custom Thread Groups" jar (jpgc-casutg)
PLUGIN_THREAD_GROUPS = {
    "kg.apc.jmeter.threads.SteppingThreadGroup": "stepping",
    "kg.apc.jmeter.threads.UltimateThreadGroup": "ultimate",
    "com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup": "concurrency",
}


def set_jmeter_path(path: str | None) -> None:
    """Use the JMeter install configured in Settings; empty or "jmeter" means search as usual."""
    global _configured_path
    p = (path or "").strip()
    _configured_path = p if p and p != "jmeter" else None


def _resolve_jmeter(path: str) -> str | None:
    """Accept the jmeter executable, its bin/ directory or the install root."""
    p = Path(path).expanduser()
    for cand in (p, p / "jmeter", p / "bin" / "jmeter"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def find_jmeter() -> str | None:
    """Return the path to the jmeter executable, or None if not found."""
    # 1. The install configured in Settings
    if _configured_path:
        found = _resolve_jmeter(_configured_path)
        if found:
            return found

    # 2. Respect explicit env var
    env_path = os.environ.get("JMETER_BIN") or os.environ.get("JMETER_HOME")
    if env_path:
        found = _resolve_jmeter(env_path)
        if found:
            return found

    # 3. Check PATH
    found = shutil.which("jmeter")
    if found:
        return found

    # 4. Fall back to known locations
    for loc in _SEARCH_PATHS:
        if Path(loc).exists():
            return loc

    return None


def jmeter_info() -> dict:
    """Which JMeter will run, whether the configured path is usable, and which plugins it has."""
    jmeter = find_jmeter()
    home = Path(os.path.realpath(jmeter)).parent.parent if jmeter else None
    ext = home / "lib" / "ext" if home else None
    return {
        "found": jmeter is not None,
        "path": jmeter,
        "home": str(home) if home else None,
        "configured_path": _configured_path,
        "configured_valid": bool(_configured_path and _resolve_jmeter(_configured_path)),
        "custom_thread_groups": bool(ext and any(ext.glob("jmeter-plugins-casutg*.jar"))),
    }


def required_plugin(jmx_path: str) -> str | None:
    """The thread-group type of a JMX that needs JMeter Plugins, or None for built-in elements."""
    root = ET.parse(jmx_path).getroot()
    for el in root.iter():
        if el.tag in PLUGIN_THREAD_GROUPS and el.get("enabled", "true") == "true":
            return PLUGIN_THREAD_GROUPS[el.tag]
    return None


def _set_prop(parent: ET.Element, tag: str, name: str, value: str) -> None:
    """Set (or create) a <stringProp>/<boolProp> child by name."""
    for prop in parent.findall(tag):
        if prop.attrib.get("name") == name:
            prop.text = value
            return
    el = ET.SubElement(parent, tag, name=name)
    el.text = value


def _override_thread_settings(
    jmx_path: str,
    threads: int | None,
    ramp_up: int | None,
    loops: int | None,
    duration_seconds: int | None = None,
) -> str:
    """
    Write a temporary JMX with updated ThreadGroup settings and return its path.
    The caller is responsible for deleting the temp file.

    loops = -1 means "loop forever" and needs a duration; duration_seconds > 0
    turns on the ThreadGroup scheduler, 0 turns it off.
    """
    tree = ET.parse(jmx_path)
    root = tree.getroot()

    for tg in root.iter("ThreadGroup"):
        if threads is not None:
            _set_prop(tg, "stringProp", "ThreadGroup.num_threads", str(threads))
        if ramp_up is not None:
            _set_prop(tg, "stringProp", "ThreadGroup.ramp_time", str(ramp_up))
        loop_ctrl = tg.find("elementProp[@name='ThreadGroup.main_controller']")
        if loop_ctrl is not None and loops is not None:
            _set_prop(loop_ctrl, "stringProp", "LoopController.loops", str(loops))
            _set_prop(loop_ctrl, "boolProp", "LoopController.continue_forever", "true" if loops == -1 else "false")
        if duration_seconds is not None:
            on = duration_seconds > 0
            _set_prop(tg, "boolProp", "ThreadGroup.scheduler", "true" if on else "false")
            _set_prop(tg, "stringProp", "ThreadGroup.duration", str(duration_seconds) if on else "")
            _set_prop(tg, "stringProp", "ThreadGroup.delay", "0" if on else "")

    tmp = tempfile.NamedTemporaryFile(
        suffix=".jmx", delete=False, mode="w", encoding="utf-8"
    )
    tree.write(tmp.name, encoding="unicode", xml_declaration=True)
    tmp.close()
    return tmp.name


def _prop(el, name):
    for p in el:
        if p.attrib.get("name") == name:
            return (p.text or "").strip()
    return ""


def _to_int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def profile_summary(tg_type: str, params: dict) -> dict:
    """
    Peak users, time to reach them and total planned duration (seconds) of a
    non-standard thread group — used for timeouts and the live monitor.
    """
    p = {k: _to_int(v, 0) for k, v in params.items() if k != "rows"}
    if tg_type == "stepping":
        n = max(1, p.get("threads", 1))
        step = max(1, p.get("step_users", 1))
        burst = min(p.get("start_burst", 0) or step, n)
        cycle = p.get("step_period", 0) + p.get("step_rampup", 0)
        steps = math.ceil((n - burst) / step) if n > burst else 0
        to_peak = p.get("initial_delay", 0) + steps * cycle + p.get("step_rampup", 0)
        stop = math.ceil(n / max(1, p.get("stop_users", 0) or n)) * p.get("stop_period", 0)
        return {"peak": n, "to_peak": to_peak, "duration": to_peak + p.get("hold", 0) + stop}
    if tg_type == "concurrency":
        ramp = p.get("ramp_up", 0)
        return {"peak": max(1, p.get("target", 1)), "to_peak": ramp, "duration": ramp + p.get("hold", 0)}
    if tg_type == "ultimate":
        rows = [{k: _to_int(v, 0) for k, v in r.items()} for r in params.get("rows") or []]
        if not rows:
            return {"peak": 1, "to_peak": 0, "duration": 0}
        ends = [r["initial_delay"] + r["startup"] + r["hold"] + r["shutdown"] for r in rows]
        # Peak concurrency happens at a moment when some row has just finished ramping up
        def users_at(t):
            total = 0
            for r in rows:
                s, up, hold, down = r["initial_delay"], r["startup"], r["hold"], r["shutdown"]
                if t < s or t > s + up + hold + down:
                    continue
                if t < s + up:
                    total += r["threads"] * (t - s) / up if up else r["threads"]
                elif t <= s + up + hold:
                    total += r["threads"]
                else:
                    total += r["threads"] * (1 - (t - s - up - hold) / down) if down else 0
            return total
        marks = sorted({r["initial_delay"] + r["startup"] for r in rows})
        peak_t = max(marks, key=users_at)
        return {"peak": max(1, round(users_at(peak_t))), "to_peak": peak_t, "duration": max(ends)}
    return {"peak": 1, "to_peak": 0, "duration": 0}


def read_thread_group(jmx_path: str) -> dict:
    """Thread-group type of a JMX and its parameters, as the generator's `thread_group` argument."""
    root = ET.parse(jmx_path).getroot()
    el = next((e for e in root.iter() if e.tag in PLUGIN_THREAD_GROUPS), None)
    if el is None:
        return {"type": "standard"}
    tg_type = PLUGIN_THREAD_GROUPS[el.tag]
    if tg_type == "stepping":
        names = {"threads": "ThreadGroup.num_threads", "initial_delay": "Threads initial delay",
                 "start_burst": "Start users count burst", "step_users": "Start users count",
                 "step_period": "Start users period", "step_rampup": "rampUp", "hold": "flighttime",
                 "stop_users": "Stop users count", "stop_period": "Stop users period"}
        return {"type": tg_type, **{k: _to_int(_prop(el, v), 0) for k, v in names.items()}}
    if tg_type == "concurrency":
        scale = 60 if _prop(el, "Unit") == "M" else 1
        return {"type": tg_type, "target": _to_int(_prop(el, "TargetLevel"), 1),
                "ramp_up": _to_int(_prop(el, "RampUp"), 0) * scale, "steps": _to_int(_prop(el, "Steps"), 0),
                "hold": _to_int(_prop(el, "Hold"), 0) * scale, "iterations": _to_int(_prop(el, "Iterations"), 0)}
    rows = []
    data = el.find("collectionProp[@name='ultimatethreadgroupdata']")
    for row in (data if data is not None else []):
        vals = [_to_int(p.text, 0) for p in row]
        if len(vals) >= 5:
            rows.append(dict(zip(("threads", "initial_delay", "startup", "hold", "shutdown"), vals)))
    return {"type": tg_type, "rows": rows}


def read_thread_settings(jmx_path: str) -> dict:
    """Current ThreadGroup settings of a JMX: threads, rampup, loops, duration_seconds."""
    tg_info = read_thread_group(jmx_path)
    if tg_info["type"] != "standard":
        # Plugin thread groups run for a planned time: report it as a duration test
        s = profile_summary(tg_info["type"], tg_info)
        return {"threads": s["peak"], "rampup": s["to_peak"], "loops": -1, "duration_seconds": s["duration"]}
    tg = ET.parse(jmx_path).getroot().find(".//ThreadGroup")
    if tg is None:
        return {}

    prop, to_int = _prop, _to_int

    loop_ctrl = tg.find("elementProp[@name='ThreadGroup.main_controller']")
    loops = to_int(prop(loop_ctrl, "LoopController.loops"), 1) if loop_ctrl is not None else 1
    if loop_ctrl is not None and prop(loop_ctrl, "LoopController.continue_forever") == "true" and loops < 0:
        loops = -1
    scheduler = prop(tg, "ThreadGroup.scheduler") == "true"
    return {
        "threads": to_int(prop(tg, "ThreadGroup.num_threads"), 1),
        "rampup": to_int(prop(tg, "ThreadGroup.ramp_time"), 1),
        "loops": loops,
        "duration_seconds": to_int(prop(tg, "ThreadGroup.duration"), 0) if scheduler else 0,
    }


def stop_jmeter(graceful: bool = True) -> bool:
    """
    Ask a running non-GUI JMeter to stop via its shutdown scripts (UDP port 4445).
    Graceful lets in-flight samples finish and still produces the HTML report.
    """
    jmeter = find_jmeter()
    if not jmeter:
        return False
    script = Path(os.path.realpath(jmeter)).parent / ("shutdown.sh" if graceful else "stoptest.sh")
    if not script.exists():
        return False
    try:
        subprocess.run([str(script)], capture_output=True, text=True, timeout=20)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def count_samplers(jmx_path: str) -> int:
    """Number of enabled HTTP samplers in a JMX (for progress estimates)."""
    try:
        root = ET.parse(jmx_path).getroot()
    except ET.ParseError:
        return 0
    return sum(1 for el in root.iter("HTTPSamplerProxy") if el.get("enabled", "true") == "true")


def run_jmeter(
    jmx_path: str,
    output_dir: str | Path | None = None,
    threads: int | None = None,
    ramp_up: int | None = None,
    loops: int | None = None,
    timeout: int = 3600,
    duration_seconds: int | None = None,
    html_report_dir: str | Path | None = None,
    save_responses: bool = False,
    extra_props: dict[str, str] | None = None,
) -> dict:
    """
    Run a JMX test plan with jMeter in non-GUI mode.

    Args:
        jmx_path:   Path to the .jmx file.
        output_dir: Directory for results (default: temp dir).
        threads:    Override ThreadGroup thread count.
        ramp_up:    Override ramp-up period in seconds.
        loops:      Override loop count (-1 = until the duration ends).
        duration_seconds: Override scheduler duration (0 = run by loop count).
        timeout:    Max seconds to wait for jMeter to finish.
        save_responses: Write an XML JTL including request/response headers and
                    bodies (used by replay verification). Not for load tests.
        extra_props: Additional -J JMeter properties.

    Returns:
        Dict with keys: jtl_path, log_path, returncode, stdout, stderr.

    Raises:
        FileNotFoundError: If jMeter executable is not found.
        FileNotFoundError: If jmx_path does not exist.
    """
    jmeter = find_jmeter()
    if not jmeter:
        raise FileNotFoundError(
            "jMeter not found. Install it or set JMETER_BIN=/path/to/jmeter."
        )

    if not Path(jmx_path).exists():
        raise FileNotFoundError(f"JMX file not found: {jmx_path}")

    # Apply overrides if any were specified
    temp_jmx = None
    run_jmx = jmx_path
    if any(v is not None for v in (threads, ramp_up, loops, duration_seconds)):
        temp_jmx = _override_thread_settings(jmx_path, threads, ramp_up, loops, duration_seconds)
        run_jmx = temp_jmx

    # Set up output directory
    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="pstack_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    jtl_path = str(out_dir / "results.jtl")
    log_path = str(out_dir / "jmeter.log")
    # JMeter appends to an existing results file, and refuses to build the HTML
    # report from a non-empty one — a re-run would silently keep the previous
    # run's results. Every run starts from clean files.
    for stale in (jtl_path, log_path):
        Path(stale).unlink(missing_ok=True)

    cmd = [
        jmeter,
        "-n",               # non-GUI mode
        "-t", run_jmx,      # test plan
        "-l", jtl_path,     # results (JTL/CSV)
        "-j", log_path,     # jMeter log
        "-Jjmeter.save.saveservice.response_code=true",
        "-Jjmeter.save.saveservice.success=true",
        "-Jjmeter.save.saveservice.time=true",
        "-Jjmeter.save.saveservice.label=true",
        "-Jjmeter.save.saveservice.thread_name=true",
    ]
    if save_responses:
        cmd += [
            "-Jjmeter.save.saveservice.output_format=xml",
            "-Jjmeter.save.saveservice.response_data=true",
            "-Jjmeter.save.saveservice.samplerData=true",
            "-Jjmeter.save.saveservice.requestHeaders=true",
            "-Jjmeter.save.saveservice.responseHeaders=true",
            "-Jjmeter.save.saveservice.url=true",
            "-Jjmeter.save.saveservice.assertion_results_failure_message=true",
            "-Jjmeter.save.saveservice.assertion_results=all",
            "-Jjmeter.save.saveservice.subresults=true",
        ]
    else:
        cmd.append("-Jjmeter.save.saveservice.output_format=csv")
        # Write every sample immediately so the live monitor can tail the file
        cmd.append("-Jjmeter.save.saveservice.autoflush=true")
    for k, v in (extra_props or {}).items():
        cmd.append(f"-J{k}={v}")

    # HTML dashboard report generation (-e -o)
    if html_report_dir:
        html_path = Path(html_report_dir)
        # JMeter requires the output directory to be empty or non-existent
        if html_path.exists():
            shutil.rmtree(html_path)
        # Its scratch folder defaults to temp/ in the working directory, which may not be writable
        cmd += ["-e", "-o", str(html_path), f"-Jjmeter.reportgenerator.temp_dir={out_dir / 'report-temp'}"]

    env = None
    if save_responses:
        # Replays are tiny (1 user × 1 iteration), so JVM start-up dominates: C1-only JIT
        # and class-data sharing cut ~20% off each run. Not used for load tests, where
        # the full JIT matters for throughput.
        env = os.environ.copy()
        env["JVM_ARGS"] = (env.get("JVM_ARGS", "") + " -XX:TieredStopAtLevel=1 -Xshare:auto").strip()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    finally:
        if temp_jmx and Path(temp_jmx).exists():
            Path(temp_jmx).unlink()

    return {
        "jtl_path": jtl_path,
        "log_path": log_path,
        "output_dir": str(out_dir),
        "html_report_dir": str(html_report_dir) if html_report_dir else None,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
