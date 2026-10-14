"""
Replay verification and auto-correlation ("replay & fix").

LoadRunner's strongest correlation feature is its replay scan: run the script
once, diff the replay against the recording and flag what changed. This
module does the same for JMeter and then goes one step further — it fixes
what it finds and replays again until the script is stable:

  1. Generate the JMX for 1 user × 1 iteration, with each sampler labelled by
     its HAR index and a logger that records every extracted variable.
  2. Run JMeter, saving full request/response data (XML JTL).
  3. Compare every replayed exchange with the recording:
       • existing correlations — does the extractor still find a value on the
         live response? Did the value change (verified dynamic) or not
         (static)? If extraction fails, pick an alternative extractor that
         works on both the recorded and the live response (repair).
       • hard-coded values — anything a request sends that came from an
         earlier response and changed on replay is a missed correlation;
         it is added automatically.
       • failures — requests whose status or assertions no longer match, with
         hints for values that appear in no recorded response at all.
  4. Repeat with the updated correlations (max_iterations).
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from projectstack.correlator.engine import CorrelatedValue, _is_dynamic, _is_route_segment, _make_var
from projectstack.correlator.matching import (
    apply_extractor, candidate_extractors, contains_value, find_encoding,
    request_haystack, request_values,
)
from projectstack.generator.jmx_generator import generate_jmx
from projectstack.runner.jmeter_runner import run_jmeter

_TRIVIAL_VALUES = {"true", "false", "null", "none", "undefined", "on", "off", "yes", "no"}


# ---------------------------------------------------------------------------
# Replay results
# ---------------------------------------------------------------------------

@dataclass
class ReplaySample:
    idx: int
    label: str
    status: str
    success: bool
    message: str = ""
    failures: list[str] = field(default_factory=list)
    response_body: str = ""
    response_headers: dict[str, str] = field(default_factory=dict)
    sub_headers: list[dict[str, str]] = field(default_factory=list)   # followed redirects
    request_url: str = ""
    request_body: str = ""
    request_headers: dict[str, str] = field(default_factory=dict)


def _parse_headers(text: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in (text or "").splitlines()[1:] if (text or "").startswith("HTTP") else (text or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if k in headers and k.lower() == "set-cookie":
            headers[k] += "\n" + v
        else:
            headers[k] = v
    return headers


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return child.text or "" if child is not None else ""


def parse_xml_jtl(path: str | Path) -> dict[int, ReplaySample]:
    """Parse an XML JTL written in replay mode; keys are HAR request indices."""
    samples: dict[int, ReplaySample] = {}
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, FileNotFoundError):
        return samples
    for el in root:
        if el.tag not in ("httpSample", "sample"):
            continue
        label = el.get("lb", "")
        if not label.startswith("["):
            continue
        try:
            idx = int(label[1:label.index("]")])
        except ValueError:
            continue
        failures = []
        for ar in el.findall("assertionResult"):
            if _text(ar, "failure") == "true" or _text(ar, "error") == "true":
                failures.append(f"{_text(ar, 'name')}: {_text(ar, 'failureMessage')}".strip(": "))
        subs = [_parse_headers(_text(sub, "responseHeader")) for sub in el.findall("httpSample")]
        samples[idx] = ReplaySample(
            idx=idx,
            label=label,
            status=el.get("rc", ""),
            success=el.get("s") == "true",
            message=el.get("rm", ""),
            failures=failures,
            response_body=_text(el, "responseData"),
            response_headers=_parse_headers(_text(el, "responseHeader")),
            sub_headers=subs,
            request_url=_text(el, "java.net.URL"),
            request_body=_text(el, "queryString"),
            request_headers=_parse_headers(_text(el, "requestHeader")),
        )
    return samples


def parse_var_log(path: str | Path) -> dict[int, dict[str, str | None]]:
    out: dict[int, dict[str, str | None]] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
            out[int(rec["idx"])] = rec.get("vars") or {}
        except (ValueError, KeyError, TypeError):
            continue
    return out


def run_replay(
    requests: list,
    correlations: list[CorrelatedValue],
    work_dir: str | Path,
    selected_indices: list[int] | None = None,
    timeout: int = 300,
) -> tuple[dict[int, ReplaySample], dict[int, dict], dict]:
    """Generate the replay JMX, run it once and return (samples, var_log, run_info)."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    jmx_path = work / "replay.jmx"
    var_log = work / "vars.jsonl"
    if var_log.exists():
        var_log.unlink()
    generate_jmx(
        requests=requests,
        correlated_values=correlations,
        output_path=jmx_path,
        test_name="ProjectStack Replay",
        threads=1, ramp_up=1, loops=1,
        include_view_tree=False,
        no_transactions=True,
        include_assertions=True,
        selected_indices=selected_indices,
        label_with_index=True,
        correlation_markers=False,     # replay checks extracted values itself
    )
    run_dir = work / "run"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    info = run_jmeter(
        jmx_path=str(jmx_path),
        output_dir=str(run_dir),
        timeout=timeout,
        save_responses=True,
        extra_props={"pstack.varlog": str(var_log)},
    )
    return parse_xml_jtl(info["jtl_path"]), parse_var_log(var_log), info


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _status_matches(recorded: int, replayed: str) -> bool:
    try:
        live = int(replayed)
    except (TypeError, ValueError):
        return False
    if live == recorded:
        return True
    # Same class of success is fine (200 vs 201, or a followed 302 that ended in 200)
    return 200 <= live < 300 and 200 <= recorded < 400


def _live_value(cv: CorrelatedValue, sample: ReplaySample | None) -> str | None:
    if sample is None or not cv.extractor or cv.extractor == "function":
        return None
    v = apply_extractor(cv.extractor, cv.extractor_config or {}, sample.response_body, sample.response_headers)
    if v is None and cv.extractor in ("header", "header_regex", "cookie_header"):
        for h in sample.sub_headers:
            v = apply_extractor(cv.extractor, cv.extractor_config or {}, "", h)
            if v is not None:
                break
    return v


_MARKUP_CHARS = set("<>\"'\r\n\t ")


def _plausible(recorded: str, live: str) -> bool:
    """
    Could `live` be a new run's version of `recorded`? An extractor that matched the
    wrong text returns markup, whitespace or a much longer string — that is a broken
    extractor, not a value that changed.
    """
    if any(c in _MARKUP_CHARS and c not in recorded for c in live):
        return False
    return len(live) <= max(3 * len(recorded), len(recorded) + 64)


def _short(path_or_url: str) -> str:
    p = urlparse(path_or_url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def analyze_replay(
    requests: list,
    correlations: list[CorrelatedValue],
    samples: dict[int, ReplaySample],
    var_log: dict[int, dict],
    order: list[int],
) -> dict:
    """Compare replay with recording; return findings plus the updated correlation list."""
    cvs = copy.deepcopy(correlations)
    known_values = {cv.extract_value for cv in cvs}
    used_names = {cv.var_name for cv in cvs}
    position = {idx: n for n, idx in enumerate(order)}

    verified, static, repaired, broken, added = [], [], [], [], []

    # ── 1. Existing correlations ─────────────────────────────────────────────
    for cv in cvs:
        if cv.source_request_index < 0 or cv.source_request_index not in samples:
            continue
        j = cv.source_request_index
        sample = samples[j]
        logged = (var_log.get(j) or {}).get(cv.var_name)
        live = _live_value(cv, sample)
        if live is None and logged not in (None, "NOT_FOUND"):
            live = logged
        if live is not None and live != "NOT_FOUND" and not _plausible(cv.extract_value, live):
            live = None   # matched the wrong text — repair it below
        if live is not None and live != "NOT_FOUND":
            if live != cv.extract_value:
                verified.append(cv.var_name)
                if "replay_verified" not in cv.detection_pass:
                    cv.detection_pass += ",replay_verified"
            else:
                static.append(cv.var_name)
            continue
        if not _status_matches(requests[j].status_code, sample.status):
            continue  # the source request itself failed; fix that first
        # Extraction failed on a healthy response — find an extractor that works on both
        for spec in candidate_extractors(requests[j], cv.extract_value):
            if (spec["extractor"], spec["config"]) == (cv.extractor, cv.extractor_config):
                continue
            rec_val = apply_extractor(spec["extractor"], spec["config"], requests[j].response_body,
                                      requests[j].response_headers)
            new_val = apply_extractor(spec["extractor"], spec["config"], sample.response_body,
                                      sample.response_headers)
            if rec_val == cv.extract_value and new_val and _plausible(cv.extract_value, new_val):
                old = cv.extractor or "legacy"
                cv.extractor, cv.extractor_config = spec["extractor"], spec["config"]
                cv.extract_key = spec["extract_key"]
                if "replay_repaired" not in cv.detection_pass:
                    cv.detection_pass += ",replay_repaired"
                repaired.append({"var_name": cv.var_name, "from": old, "to": spec["extractor"]})
                break
        else:
            broken.append({
                "var_name": cv.var_name, "source_request_index": j,
                "reason": "Extractor found nothing usable in the replayed response and no alternative matched",
            })

    # ── 2. Hard-coded values that changed on replay → new correlations ───────
    hay = [request_haystack(r) for r in requests]
    for k in order:
        req = requests[k]
        for loc, value in request_values(req):
            if value in known_values or value.lower() in _TRIVIAL_VALUES:
                continue
            key = loc.split(":", 1)[-1]
            # Replay confirms dynamism directly, so the look-based filter is relaxed to
            # also admit short IDs (any value of 3+ chars containing a digit).
            if len(value) < 3 or not (_is_dynamic(value, key) or any(c.isdigit() for c in value)):
                continue
            if loc == "path" and _is_route_segment(value):
                continue
            for j in sorted((i for i in order if position[i] < position[k]), key=lambda i: position[i]):
                src = requests[j]
                if not (contains_value(src.response_body, value)
                        or any(contains_value(h, value) for h in (src.response_headers or {}).values())):
                    continue
                sample = samples.get(j)
                if sample is None:
                    break
                for spec in candidate_extractors(src, value):
                    live = apply_extractor(spec["extractor"], spec["config"], sample.response_body,
                                           sample.response_headers)
                    if live is None or not _plausible(value, live):
                        continue
                    if live != value:
                        name = _make_var(key.split(".")[-1] if not loc.startswith("path") else f"path_{value[:8]}")
                        base, n = name, 1
                        while name in used_names:
                            n += 1
                            name = f"{base}_{n}"
                        used_names.add(name)
                        uses = [u for u in order if position[u] > position[j] and find_encoding(hay[u], value)]
                        cv = CorrelatedValue(
                            var_name=name,
                            source_request_index=j,
                            extract_from=spec["extract_from"],
                            extract_key=spec["extract_key"],
                            extract_value=value,
                            used_in_requests=uses,
                            detection_pass="pass_replay,replay_verified",
                            reason=f"Changed on replay ({value[:24]} → {live[:24]}); sent as {loc}",
                            extractor=spec["extractor"],
                            extractor_config=spec["config"],
                        )
                        cvs.append(cv)
                        known_values.add(value)
                        added.append({"var_name": name, "source_request_index": j, "used_in_requests": uses,
                                      "recorded": value[:80], "replayed": live[:80]})
                    break
                break

    # ── 3. Per-request results and hints ────────────────────────────────────
    results = []
    for k in order:
        req = requests[k]
        s = samples.get(k)
        if s is None:
            results.append({"index": k, "method": req.method, "path": _short(req.url),
                            "recorded_status": req.status_code, "replay_status": None,
                            "ok": False, "reason": "Not executed (an earlier error stopped the thread?)"})
            continue
        ok = s.success and _status_matches(req.status_code, s.status)
        reason = ""
        if not ok:
            reason = "; ".join(s.failures) or f"Status {s.status} (recorded {req.status_code}) {s.message}".strip()
        missing = [n for n, v in (var_log.get(k) or {}).items() if v in (None, "NOT_FOUND")]
        hints = []
        if missing:
            hints.append(f"Extractor returned nothing for {', '.join(missing)}")
        # Correlated values this request sent that the replay extracted as garbage
        for cv in cvs:
            if k not in cv.used_in_requests or cv.source_request_index < 0:
                continue
            got = (var_log.get(cv.source_request_index) or {}).get(cv.var_name)
            if got not in (None, "NOT_FOUND") and not _plausible(cv.extract_value, got):
                shown = " ".join(got.split())[:50]
                hints.append(
                    f"${{{cv.var_name}}} was extracted as \"{shown}…\" (recorded \"{cv.extract_value[:30]}\") — "
                    f"its extractor on request #{cv.source_request_index + 1} matched the wrong text"
                )
        if _correlated_names_sent(cvs, k) and "NOT_FOUND" in (s.request_url + s.request_body):
            hints.append("Request was sent with NOT_FOUND in place of a correlated value")
        if not ok:
            for loc, value in request_values(req):
                if value in known_values or not _is_dynamic(value, loc.split(":", 1)[-1]):
                    continue
                if loc == "path" and _is_route_segment(value):
                    continue
                if not any(contains_value(requests[j].response_body, value)
                           or any(contains_value(h, value) for h in (requests[j].response_headers or {}).values())
                           for j in range(len(requests)) if j != k):
                    hints.append(
                        f"{loc} = {value[:40]} appears in no recorded response — it may be generated by "
                        "JavaScript or come from a request that was filtered out"
                    )
        results.append({
            "index": k, "method": req.method, "path": _short(req.url),
            "recorded_status": req.status_code, "replay_status": s.status,
            "ok": ok, "reason": reason, "hints": hints[:4],
            "response_snippet": (s.response_body or "")[:600] if not ok else "",
        })

    return {
        "correlations": cvs,
        "results": results,
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "verified": verified,
        "static": static,
        "repaired": repaired,
        "broken": broken,
        "added": added,
    }


def _correlated_names_sent(cvs: list[CorrelatedValue], idx: int) -> list[str]:
    return [cv.var_name for cv in cvs if idx in cv.used_in_requests]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def replay_and_fix(
    requests: list,
    correlations: list[CorrelatedValue],
    selected_indices: list[int] | None = None,
    max_iterations: int = 5,
    work_dir: str | Path | None = None,
    timeout: int = 300,
) -> dict:
    """
    Replay the script, fix what can be fixed, and repeat until nothing changes.
    Each replay can only see past the first broken step (a failed login hides
    everything after it), so layered problems take one iteration per layer.

    Returns {"correlations": [...], "iterations": [...], "final": {...},
             "unverified_changes": bool}  — the latter is True when the last
    iteration still changed correlations that no replay has confirmed yet.
    """
    order = (
        [i for i in selected_indices if 0 <= i < len(requests)]
        if selected_indices is not None else list(range(len(requests)))
    )
    base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="pstack_replay_"))
    cvs = copy.deepcopy(correlations)
    iterations = []
    analysis: dict = {}
    best: tuple[int, list, dict] | None = None   # (failed, correlations replayed, analysis)
    for it in range(1, max(1, max_iterations) + 1):
        samples, var_log, info = run_replay(requests, cvs, base / f"iter{it}", order, timeout)
        if not samples:
            stderr = (info.get("stderr") or "").strip()[-800:]
            raise RuntimeError(
                "JMeter produced no results for the replay"
                + (f": {stderr}" if stderr else " — check the JMeter path in Settings")
            )
        analysis = analyze_replay(requests, cvs, samples, var_log, order)
        if best is not None and analysis["failed"] > best[0]:
            # This iteration's changes made things worse — roll them back and stop.
            iterations.append({
                "iteration": it, "passed": analysis["passed"], "failed": analysis["failed"],
                "added": [], "repaired": [], "broken": [], "verified_count": 0, "static_count": 0,
                "rolled_back": True,
            })
            cvs, analysis = best[1], best[2]
            break
        best = (analysis["failed"], cvs, analysis)
        cvs = analysis["correlations"]
        iterations.append({
            "iteration": it,
            "passed": analysis["passed"],
            "failed": analysis["failed"],
            "added": analysis["added"],
            "repaired": analysis["repaired"],
            "broken": analysis["broken"],
            "verified_count": len(analysis["verified"]),
            "static_count": len(analysis["static"]),
        })
        if not analysis["added"] and not analysis["repaired"]:
            break

    final = {k: v for k, v in analysis.items() if k != "correlations"}
    last = iterations[-1] if iterations else {}
    return {
        "correlations": cvs,
        "iterations": iterations,
        "final": final,
        "unverified_changes": bool(last.get("added") or last.get("repaired")) and not last.get("rolled_back"),
        "work_dir": str(base),
    }
