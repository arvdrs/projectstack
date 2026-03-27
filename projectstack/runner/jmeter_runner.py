"""
jMeter runner.

Finds jMeter on the system, optionally overrides ThreadGroup settings
in the JMX, and executes the test in non-GUI mode, returning the path
to the generated JTL results file.
"""

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


def find_jmeter() -> str | None:
    """Return the path to the jmeter executable, or None if not found."""
    # 1. Respect explicit env var
    env_path = os.environ.get("JMETER_BIN") or os.environ.get("JMETER_HOME")
    if env_path:
        candidate = Path(env_path) / "bin" / "jmeter"
        if candidate.exists():
            return str(candidate)
        if Path(env_path).is_file():
            return env_path

    # 2. Check PATH
    found = shutil.which("jmeter")
    if found:
        return found

    # 3. Fall back to known locations
    for loc in _SEARCH_PATHS:
        if Path(loc).exists():
            return loc

    return None


def _override_thread_settings(
    jmx_path: str,
    threads: int | None,
    ramp_up: int | None,
    loops: int | None,
) -> str:
    """
    Write a temporary JMX with updated ThreadGroup settings and return its path.
    The caller is responsible for deleting the temp file.
    """
    ET.register_namespace("", "")
    tree = ET.parse(jmx_path)
    root = tree.getroot()

    tg = root.find(".//ThreadGroup")
    if tg is not None:
        for prop in tg.findall("stringProp"):
            n = prop.attrib.get("name", "")
            if threads is not None and n == "ThreadGroup.num_threads":
                prop.text = str(threads)
            elif ramp_up is not None and n == "ThreadGroup.ramp_time":
                prop.text = str(ramp_up)

        loop_ctrl = tg.find(".//elementProp[@name='ThreadGroup.main_controller']")
        if loop_ctrl is not None and loops is not None:
            for prop in loop_ctrl.findall("stringProp"):
                if prop.attrib.get("name") == "LoopController.loops":
                    prop.text = str(loops)

    tmp = tempfile.NamedTemporaryFile(
        suffix=".jmx", delete=False, mode="w", encoding="utf-8"
    )
    tree.write(tmp.name, encoding="unicode", xml_declaration=True)
    tmp.close()
    return tmp.name


def run_jmeter(
    jmx_path: str,
    output_dir: str | Path | None = None,
    threads: int | None = None,
    ramp_up: int | None = None,
    loops: int | None = None,
    timeout: int = 3600,
) -> dict:
    """
    Run a JMX test plan with jMeter in non-GUI mode.

    Args:
        jmx_path:   Path to the .jmx file.
        output_dir: Directory for results (default: temp dir).
        threads:    Override ThreadGroup thread count.
        ramp_up:    Override ramp-up period in seconds.
        loops:      Override loop count.
        timeout:    Max seconds to wait for jMeter to finish.

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
    if any(v is not None for v in (threads, ramp_up, loops)):
        temp_jmx = _override_thread_settings(jmx_path, threads, ramp_up, loops)
        run_jmx = temp_jmx

    # Set up output directory
    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="pstack_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    jtl_path = str(out_dir / "results.jtl")
    log_path = str(out_dir / "jmeter.log")

    cmd = [
        jmeter,
        "-n",               # non-GUI mode
        "-t", run_jmx,      # test plan
        "-l", jtl_path,     # results (JTL/CSV)
        "-j", log_path,     # jMeter log
        "-Jjmeter.save.saveservice.output_format=csv",
        "-Jjmeter.save.saveservice.response_code=true",
        "-Jjmeter.save.saveservice.success=true",
        "-Jjmeter.save.saveservice.time=true",
        "-Jjmeter.save.saveservice.label=true",
        "-Jjmeter.save.saveservice.thread_name=true",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        if temp_jmx and Path(temp_jmx).exists():
            Path(temp_jmx).unlink()

    return {
        "jtl_path": jtl_path,
        "log_path": log_path,
        "output_dir": str(out_dir),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
