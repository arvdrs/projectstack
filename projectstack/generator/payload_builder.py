"""
Payload Builder — Phase 5.

Handles request body parameterisation after correlation:

  1. Simple substitution  — replace every hardcoded dynamic value with ${C_VAR}
  2. Complex body builder — for large bodies (>5 KB) or those containing nested
                            JSON-as-string fields, generates a JSR223 PreProcessor
                            Groovy script that constructs the body at runtime
  3. JSON-in-JSON escape  — when a field value is itself a JSON string, the inner
                            JSON is properly escaped for embedding as a string value

The main entry point is `build_payloads()` which processes all requests and
returns a map of  {request_index: PayloadResult}.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import CorrelatedValue


# Threshold above which we generate a Groovy body-builder instead of inline substitution
COMPLEX_BODY_THRESHOLD_BYTES = 5 * 1024  # 5 KB


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PayloadResult:
    """Result of building a single request's payload."""
    request_index: int
    final_body: str                     # Body to embed in the JMX sampler (may contain ${C_VAR})
    groovy_preprocessor: Optional[str] = None  # Non-None when body needs runtime construction
    body_var_name: Optional[str] = None  # e.g. C_BODY_07_02 when groovy_preprocessor is set


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_substitutions(text: str, subs: list[tuple[str, str]]) -> str:
    """Replace actual dynamic values with ${C_VAR} references."""
    for actual, var_name in subs:
        if actual and len(actual) >= 6 and actual in text:
            text = text.replace(actual, f"${{{var_name}}}")
    return text


def _has_json_in_json(body: str) -> bool:
    """
    Detect if a request body contains a field whose value is a JSON string
    (JSON embedded as a string inside another JSON object).
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(parsed, dict):
        return False
    for v in parsed.values():
        if isinstance(v, str) and len(v) > 10:
            try:
                json.loads(v)
                return True  # Found an embedded JSON string
            except (json.JSONDecodeError, ValueError):
                pass
    return False


def _escape_for_json_string(inner_json: str) -> str:
    """Escape an inner JSON string for embedding as a JSON field value."""
    return inner_json.replace("\\", "\\\\").replace('"', '\\"')


def _build_groovy_body_script(
    body_var: str,
    body: str,
    subs: list[tuple[str, str]],
) -> str:
    """
    Generate a Groovy PreProcessor script that constructs the request body
    at runtime by reading stored C_* variables and injecting them.
    """
    # Start with the body as a Groovy string, escaping properly
    escaped = body.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")

    # Replace each substitution target with Groovy string interpolation
    for actual, var_name in subs:
        if actual and len(actual) >= 6 and actual in escaped:
            groovy_ref = f'" + vars.get("{var_name}") + "'
            escaped = escaped.replace(actual, groovy_ref)

    lines = [
        f'def {body_var} = "{escaped}"',
        f'vars.put("{body_var}", {body_var})',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_payloads(
    requests: list[HarRequest],
    correlated_values: list[CorrelatedValue],
) -> dict[int, PayloadResult]:
    """
    Process all request bodies and apply correlation substitutions.

    For each POST/PUT/PATCH request with a body:
    - If the body is small and simple, inline ${C_VAR} substitution is applied.
    - If the body is >5 KB or contains nested JSON strings, a Groovy
      PreProcessor is generated and the sampler body is set to ${C_BODY_nn_mm}.

    Args:
        requests:          Parsed HAR requests.
        correlated_values: Correlation results from engine.run_correlation().

    Returns:
        Dict mapping request_index → PayloadResult for every request that has
        a non-empty body.  Requests without bodies are not included.
    """
    # Build substitution map: {req_idx: [(actual_value, var_name)]}
    subs_map: dict[int, list[tuple[str, str]]] = {}
    for cv in correlated_values:
        for req_idx in cv.used_in_requests:
            subs_map.setdefault(req_idx, []).append(
                (cv.extract_value, cv.var_name)
            )

    results: dict[int, PayloadResult] = {}

    for i, req in enumerate(requests):
        if req.method not in ("POST", "PUT", "PATCH"):
            continue
        if not req.body:
            continue

        subs = subs_map.get(i, [])
        body = req.body

        is_large = len(body.encode("utf-8")) > COMPLEX_BODY_THRESHOLD_BYTES
        is_complex = _has_json_in_json(body)

        if is_large or is_complex:
            # Generate Groovy PreProcessor for complex/large bodies
            body_var = f"C_BODY_{i:03d}"
            script = _build_groovy_body_script(body_var, body, subs)
            results[i] = PayloadResult(
                request_index=i,
                final_body=f"${{{body_var}}}",
                groovy_preprocessor=script,
                body_var_name=body_var,
            )
        else:
            # Simple inline substitution
            parameterised = _apply_substitutions(body, subs)
            results[i] = PayloadResult(
                request_index=i,
                final_body=parameterised,
            )

    return results


def get_preprocessor_script(result: PayloadResult) -> Optional[str]:
    """
    Return a complete JSR223 PreProcessor Groovy script for a PayloadResult,
    or None if no preprocessor is needed (simple substitution was sufficient).
    """
    return result.groovy_preprocessor
