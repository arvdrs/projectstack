"""
Multi-pass correlation engine.

Takes a list of HarRequest objects and produces a list of CorrelatedValue
objects — each representing a dynamic value that needs to be extracted from
one response and injected into subsequent requests.

Pass 1: JWT / Bearer token extraction
Pass 2: Full JSON key scanning in response bodies
Pass 3: Response headers and cookies
Pass 4: Plain text responses (regex patterns)
Pass 5: Backward inference — find where extracted values are actually used
Pass 6: Ollama AI detection for anything the rule-based passes missed
"""

import json
import re
from dataclasses import dataclass, field
from projectstack.parser.har_parser import HarRequest


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CorrelatedValue:
    """A dynamic value that must be extracted and re-used across requests."""

    var_name: str           # JMeter variable name, e.g. VAR_access_token
    source_request_index: int   # Which response to extract from (0-based)
    extract_from: str       # "body", "header", or "cookie"
    extract_key: str        # JSON key, header name, or cookie name
    extract_value: str      # The actual value (for reference / validation)
    used_in_requests: list[int] = field(default_factory=list)  # Where it's injected
    detection_pass: str = ""    # Which pass found it
    reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
BEARER_RE = re.compile(r"Bearer\s+([\w\-\.]+)", re.IGNORECASE)


def _safe_json(text: str | None) -> dict | list | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _flatten_json(obj, prefix="") -> dict[str, str]:
    """Recursively flatten a JSON object to {dotted.key: value} pairs."""
    result = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                result.update(_flatten_json(v, full_key))
            elif v is not None:
                result[full_key] = str(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            result.update(_flatten_json(item, f"{prefix}[{i}]"))
    return result


def _make_var(key: str) -> str:
    """Turn a key into a safe JMeter variable name."""
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", key)
    return f"VAR_{clean}"


def _is_dynamic(value: str) -> bool:
    """Heuristic: skip short, purely numeric, or obviously static values."""
    if len(value) < 8:
        return False
    if re.fullmatch(r"\d+", value):
        return False
    if value.lower() in ("application/json", "text/html", "true", "false", "null"):
        return False
    return True


# ---------------------------------------------------------------------------
# The 6 passes
# ---------------------------------------------------------------------------

def _pass1_jwt_bearer(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Extract JWT and Bearer tokens from response bodies and headers."""
    found = []
    for i, r in enumerate(requests):
        # Check Authorization header in the request — where is the token coming from?
        auth = r.headers.get("Authorization", "")
        bearer_match = BEARER_RE.search(auth)
        if bearer_match:
            token = bearer_match.group(1)
            # Find the response that produced this token
            for j in range(i):
                src = requests[j]
                if src.response_body and token in src.response_body:
                    found.append(CorrelatedValue(
                        var_name=_make_var(f"bearer_token_{j}"),
                        source_request_index=j,
                        extract_from="body",
                        extract_key="(bearer token)",
                        extract_value=token[:80],
                        used_in_requests=[i],
                        detection_pass="pass1_bearer",
                        reason="Bearer token used in Authorization header",
                    ))
                    break

        # Check response body for raw JWTs
        if r.response_body:
            for jwt in JWT_RE.findall(r.response_body):
                found.append(CorrelatedValue(
                    var_name=_make_var(f"jwt_{i}"),
                    source_request_index=i,
                    extract_from="body",
                    extract_key="(jwt)",
                    extract_value=jwt[:80],
                    used_in_requests=[],
                    detection_pass="pass1_jwt",
                    reason="JWT token found in response body",
                ))
    return found


def _pass2_json_keys(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Scan all JSON response bodies for dynamic-looking values."""
    found = []
    for i, r in enumerate(requests):
        parsed = _safe_json(r.response_body)
        if parsed is None:
            continue
        flat = _flatten_json(parsed)
        for key, value in flat.items():
            if _is_dynamic(value):
                found.append(CorrelatedValue(
                    var_name=_make_var(key.split(".")[-1]),
                    source_request_index=i,
                    extract_from="body",
                    extract_key=key,
                    extract_value=value[:80],
                    used_in_requests=[],
                    detection_pass="pass2_json",
                    reason=f"Dynamic JSON field: {key}",
                ))
    return found


def _pass3_headers_cookies(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Extract dynamic values from response headers and cookies."""
    found = []
    skip_headers = {
        "content-type", "content-length", "date", "server",
        "cache-control", "expires", "pragma", "connection",
        "transfer-encoding", "vary", "access-control-allow-origin",
    }
    for i, r in enumerate(requests):
        for name, value in r.response_headers.items():
            if name.lower() in skip_headers:
                continue
            if _is_dynamic(value):
                found.append(CorrelatedValue(
                    var_name=_make_var(name),
                    source_request_index=i,
                    extract_from="header",
                    extract_key=name,
                    extract_value=value[:80],
                    used_in_requests=[],
                    detection_pass="pass3_header",
                    reason=f"Dynamic response header: {name}",
                ))
        for cookie in r.cookies:
            cval = cookie.get("value", "")
            if _is_dynamic(cval):
                found.append(CorrelatedValue(
                    var_name=_make_var(cookie.get("name", "cookie")),
                    source_request_index=i,
                    extract_from="cookie",
                    extract_key=cookie.get("name", ""),
                    extract_value=cval[:80],
                    used_in_requests=[],
                    detection_pass="pass3_cookie",
                    reason=f"Dynamic cookie: {cookie.get('name')}",
                ))
    return found


def _pass4_plain_text(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Extract tokens from plain text / XML responses using regex patterns."""
    patterns = [
        ("token", re.compile(r'"token"\s*:\s*"([^"]{8,})"')),
        ("access_token", re.compile(r'"access_token"\s*:\s*"([^"]{8,})"')),
        ("refresh_token", re.compile(r'"refresh_token"\s*:\s*"([^"]{8,})"')),
        ("session_id", re.compile(r'"session(?:Id|_id)"\s*:\s*"([^"]{8,})"', re.IGNORECASE)),
    ]
    found = []
    for i, r in enumerate(requests):
        if not r.response_body:
            continue
        # Skip if already valid JSON — pass2 handles that
        if _safe_json(r.response_body) is not None:
            continue
        for name, pat in patterns:
            for match in pat.finditer(r.response_body):
                found.append(CorrelatedValue(
                    var_name=_make_var(f"{name}_{i}"),
                    source_request_index=i,
                    extract_from="body",
                    extract_key=name,
                    extract_value=match.group(1)[:80],
                    used_in_requests=[],
                    detection_pass="pass4_text",
                    reason=f"Token pattern '{name}' found in plain text response",
                ))
    return found


def _pass5_backward_inference(
    requests: list[HarRequest],
    candidates: list[CorrelatedValue],
) -> None:
    """
    For each candidate value, find which later requests actually use it.
    Updates used_in_requests in-place.
    """
    for cv in candidates:
        val = cv.extract_value
        if len(val) < 8:
            continue
        for j in range(cv.source_request_index + 1, len(requests)):
            r = requests[j]
            used = (
                val in (r.body or "")
                or any(val in v for v in r.headers.values())
                or any(val in p.get("value", "") for p in r.query_params)
            )
            if used and j not in cv.used_in_requests:
                cv.used_in_requests.append(j)


def _pass6_ollama(
    requests: list[HarRequest],
    existing_values: set[str],
    use_ollama: bool,
) -> list[CorrelatedValue]:
    """Use Mistral to catch anything the rule-based passes missed."""
    if not use_ollama:
        return []

    from projectstack.correlator.ollama_client import detect_dynamic_values

    found = []
    for i, r in enumerate(requests):
        print(f"  [ollama] Analyzing request {i + 1}/{len(requests)}: {r.method} {r.url[:60]}")
        detections = detect_dynamic_values(
            method=r.method,
            url=r.url,
            req_headers=r.headers,
            req_body=r.body,
            status=r.status_code,
            res_headers=r.response_headers,
            res_body=r.response_body,
        )
        for d in detections:
            val = d.get("value", "")
            if val and val not in existing_values and _is_dynamic(val):
                found.append(CorrelatedValue(
                    var_name=_make_var(d.get("key", f"ai_val_{i}")),
                    source_request_index=i,
                    extract_from=d.get("location", "body"),
                    extract_key=d.get("key", ""),
                    extract_value=val[:80],
                    used_in_requests=[],
                    detection_pass="pass6_ollama",
                    reason=d.get("reason", "Detected by Ollama/Mistral"),
                ))
    return found


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate(candidates: list[CorrelatedValue]) -> list[CorrelatedValue]:
    """Remove duplicate entries with the same var_name + extract_value."""
    seen: set[tuple] = set()
    unique = []
    for cv in candidates:
        key = (cv.extract_value, cv.extract_from, cv.extract_key)
        if key not in seen:
            seen.add(key)
            unique.append(cv)
    return unique


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_correlation(
    requests: list[HarRequest],
    use_ollama: bool = True,
) -> list[CorrelatedValue]:
    """
    Run all correlation passes and return deduplicated CorrelatedValue list.

    Args:
        requests:   Parsed HAR requests.
        use_ollama: Whether to run Pass 6 (Ollama AI detection).

    Returns:
        List of CorrelatedValue objects ready for JMX generation.
    """
    print("Running correlation engine...")

    all_candidates: list[CorrelatedValue] = []

    print("  Pass 1: JWT / Bearer tokens")
    all_candidates += _pass1_jwt_bearer(requests)

    print("  Pass 2: JSON response keys")
    all_candidates += _pass2_json_keys(requests)

    print("  Pass 3: Response headers and cookies")
    all_candidates += _pass3_headers_cookies(requests)

    print("  Pass 4: Plain text patterns")
    all_candidates += _pass4_plain_text(requests)

    print("  Pass 5: Backward inference (finding where values are used)")
    _pass5_backward_inference(requests, all_candidates)

    # Filter to only values actually used somewhere after extraction
    actionable = [cv for cv in all_candidates if cv.used_in_requests]

    if use_ollama:
        print("  Pass 6: Ollama AI detection")
        existing = {cv.extract_value for cv in actionable}
        ollama_found = _pass6_ollama(requests, existing, use_ollama=True)
        _pass5_backward_inference(requests, ollama_found)
        actionable += [cv for cv in ollama_found if cv.used_in_requests]

    result = _deduplicate(actionable)
    print(f"Correlation complete — {len(result)} correlated values found.")
    return result
