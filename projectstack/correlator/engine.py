"""
Multi-pass correlation engine.

Takes a list of HarRequest objects and produces a list of CorrelatedValue
objects — each representing a dynamic value that needs to be extracted from
one response and injected into subsequent requests.

Pass 1 : JWT / Bearer token extraction (+ JWT claims)
Pass 2 : Full JSON key scanning in response bodies
Pass 2b: UUIDs and numeric IDs embedded in URL paths
Pass 3 : Response headers, redirect (Location) parameters, cookie → header tokens
Pass 4 : Plain text responses (regex patterns) and HTML (hidden inputs, meta tags, form actions)
Pass R : Framework rule packs (ASP.NET, Spring, OAuth, SAML …)
Pass S : Request scan — values sent by a request that appear in an earlier response
Pass 5 : Backward inference — find where extracted values are actually used
Pass C : Client-generated values (timestamps, request UUIDs) → JMeter functions
Pass 6 : AI detection via the configured LLM provider

Value matching is boundary-aware and encoding-aware (see matching.py), and
every correlation carries a concrete extractor spec so the JMX generator
does not have to guess.
"""

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlparse

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.matching import (
    apply_extractor, contains_value, correlatable_header_items, dotted_key, expand_truncated,
    find_encoding, header_get, html_elements, infer_extractor, json_leaves,
    jregex_escape, looks_like_html, request_haystack, request_values,
    safe_json, scalar_text, smart_jsonpath, TextIndex, response_text,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CorrelatedValue:
    """A dynamic value that must be extracted and re-used across requests."""

    var_name: str                       # JMeter variable name, e.g. C_BEARER_TOKEN
    source_request_index: int           # Which response to extract from (0-based); -1 = generated client-side
    extract_from: str                   # "body", "header", "cookie" or "client"
    extract_key: str                    # JSON key, header name, selector … (human readable)
    extract_value: str                  # The actual value (for reference / validation)
    used_in_requests: list[int] = field(default_factory=list)  # Where it's injected
    detection_pass: str = ""            # Which pass found it
    reason: str = ""
    # How JMeter extracts it: json | css | header | header_regex | boundary | regex |
    # jwt_claim | cookie_header | function. Empty = legacy behaviour keyed on detection_pass.
    extractor: str = ""
    extractor_config: dict | None = None


# ---------------------------------------------------------------------------
# Module-level regex patterns
# ---------------------------------------------------------------------------

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
BEARER_RE = re.compile(r"Bearer\s+([\w\-\.~+/]+=*)", re.IGNORECASE)
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
UUID_IN_PATH_RE = UUID_RE
# Lookarounds (not consuming "/") so consecutive IDs like /1234/5678 are both found
NUM_ID_IN_PATH_RE = re.compile(r"(?:(?<=/)|^)(\d{4,})(?=/|$)")

_TOKEN_ENDPOINT_RE = re.compile(
    r"/(connect/token|oauth/token|auth/token|token)\b", re.IGNORECASE
)

# Last key segment that names an identifier (orderId, user_id, id, ref …)
_ID_KEY_RE = re.compile(
    r"(?:^|[._-])(?:id|uid|guid|uuid|key|number|no|num|ref|code)$|[a-z](?:Id|ID|No|Num|Key|Ref|Code)$",
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_STATIC_VALUES = {
    "application/json", "text/html", "text/plain", "true", "false", "null",
    "undefined", "none", "success", "ok", "active", "enabled", "disabled",
}

# Detection passes in order of preference when two passes find the same value
_PASS_PRIORITY = [
    "pass1_bearer", "pass_rule", "pass_html", "pass1_jwt_claim", "pass2_json",
    "pass1_jwt", "pass_url_id", "pass3_header", "pass3_redirect", "pass3_cookie_header",
    "pass4_text", "pass_request_scan", "pass_comparison", "pass_client",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json(text: str | None) -> dict | list | None:
    return safe_json(text)


def _flatten_json(obj, prefix="") -> dict[str, str]:
    """Recursively flatten a JSON object to {dotted.key: value} pairs."""
    result = {}
    for path, leaf in json_leaves(obj):
        key = dotted_key(path)
        if prefix:
            key = f"{prefix}.{key}" if not key.startswith("[") else prefix + key
        result[key] = scalar_text(leaf)
    return result


def _make_var(key: str) -> str:
    """
    Turn a full JSON key path into a C_ UPPER_SNAKE_CASE variable name.
    Uses the full path for uniqueness (user.id → C_USER_ID, order.id → C_ORDER_ID).
    Array indices like [0] are stripped — they add no semantic value.
    """
    clean = re.sub(r"\[\d+\]", "", key)           # strip [0], [1], …
    clean = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", clean)  # camelCase → camel_Case
    clean = re.sub(r"[^a-zA-Z0-9]", "_", clean).upper()
    clean = re.sub(r"_+", "_", clean).strip("_")
    return f"C_{clean}" if clean else "C_VAR"


def _decode_jwt_payload(jwt_str: str) -> dict:
    """
    Decode the payload segment of a JWT (the middle base64url chunk).
    Returns the claims dict, or {} on any failure.
    """
    try:
        parts = jwt_str.split(".")
        if len(parts) != 3:
            return {}
        segment = parts[1]
        segment += "=" * (-len(segment) % 4)
        decoded = base64.urlsafe_b64decode(segment).decode("utf-8")
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _is_dynamic(value: str, key: str = "") -> bool:
    """Heuristic: is this a value that could change between sessions?"""
    if not value:
        return False
    if UUID_RE.fullmatch(value) or JWT_RE.fullmatch(value):
        return True
    last_key = re.split(r"[.\[\]]", key.rstrip("]"))[-1] if key else ""
    if re.fullmatch(r"\d+", value):
        # Numeric IDs: short ones only when the key says it is an identifier
        if last_key and _ID_KEY_RE.search(last_key) and len(value) >= 3:
            return True
        return len(value) >= 6
    if len(value) < 6:
        return False
    if value.lower() in _STATIC_VALUES:
        return False
    if " " in value.strip():          # human-readable text, not a token
        return False
    # Plain words ("callback", "Bearer", "ACTIVE") are vocabulary, not session data
    if value.isalpha() and len(value) < 24 and (value.islower() or value.isupper() or value.istitle()):
        return False
    if _DATE_RE.fullmatch(value):
        return False
    return True


# A URL path segment made of words, optionally with a file extension, names a page
# or route ("Order.action", "checkout", "product-list") — it is not session data,
# even though it appears in an earlier response as a link.
_ROUTE_SEGMENT_RE = re.compile(r"[A-Za-z][A-Za-z_\-]*(?:\.[A-Za-z]{1,8})?")


def _is_route_segment(value: str) -> bool:
    return len(value) <= 64 and bool(_ROUTE_SEGMENT_RE.fullmatch(value))


def _find_token_key(response_body: str, token: str) -> str:
    """
    Find the actual JSON field name that holds `token` in a response body.
    Falls back to 'access_token' if the body isn't JSON or the token isn't found.
    """
    COMMON_TOKEN_FIELDS = (
        "access_token", "token", "id_token", "jwt", "auth_token",
        "bearer_token", "sessionToken", "session_token", "authToken",
    )
    try:
        flat = _flatten_json(json.loads(response_body))
        for preferred in COMMON_TOKEN_FIELDS:
            if flat.get(preferred) == token:
                return preferred
        for k, v in flat.items():
            if v == token:
                return k
    except Exception:
        pass
    return "access_token"


def _haystacks(requests: list[HarRequest]) -> TextIndex:
    """Index of everything each request sends (URL, body, headers)."""
    return TextIndex([request_haystack(r) for r in requests])


def _responses(requests: list[HarRequest]) -> TextIndex:
    """Index of everything each response returns (body and header values)."""
    return TextIndex([response_text(r) for r in requests])


def _uses(hay: TextIndex, value: str, after: int) -> list[int]:
    """Indices of requests after `after` that send `value` (raw or encoded)."""
    return hay.docs_any_encoding(value, after + 1)


def _with_extractor(cv: CorrelatedValue, spec: dict | None) -> CorrelatedValue:
    if spec:
        cv.extractor = spec["extractor"]
        cv.extractor_config = spec["config"]
        cv.extract_from = spec.get("extract_from", cv.extract_from)
    return cv


# ---------------------------------------------------------------------------
# Pass 1: JWT / Bearer tokens
# ---------------------------------------------------------------------------

def _pass1_jwt_bearer(requests: list[HarRequest], sent: "TextIndex | None" = None) -> list[CorrelatedValue]:
    """
    Extract JWT / Bearer tokens from responses and trace their use in
    Authorization headers. Also decodes JWT payloads to emit correlations for
    sub / userId / nameid / oid / email — claims that typically appear in API paths.
    """
    found: list[CorrelatedValue] = []
    recorded: set[tuple] = set()

    JWT_CLAIM_FIELDS = ("sub", "userId", "user_id", "nameid", "oid",
                        "email", "upn", "preferred_username", "tid", "sid")

    def _emit_jwt_claims(jwt_str: str, source_idx: int) -> None:
        nonlocal sent
        claims = _decode_jwt_payload(jwt_str)
        for field_name in JWT_CLAIM_FIELDS:
            raw = claims.get(field_name)
            if raw is None or isinstance(raw, (dict, list, bool)):
                continue
            val = str(raw)
            if len(val) < 4:
                continue
            claim_key = (source_idx, field_name, val)
            if claim_key in recorded:
                continue
            recorded.add(claim_key)
            if sent is None:
                sent = _haystacks(requests)
            uses = sent.docs(val, source_idx + 1)
            if uses:
                found.append(CorrelatedValue(
                    var_name=_make_var(field_name),
                    source_request_index=source_idx,
                    extract_from="body",
                    extract_key=f"jwt.payload.{field_name}",
                    extract_value=val,
                    used_in_requests=uses,
                    detection_pass="pass1_jwt_claim",
                    reason=f"JWT claim '{field_name}' used in {len(uses)} subsequent request(s)",
                    extractor="jwt_claim",
                    extractor_config={"claim": field_name},
                ))

    # ── Loop 1: Bearer tracing ────────────────────────────────────────────────
    for i, r in enumerate(requests):
        auth = header_get(r.headers, "Authorization")
        bearer_match = BEARER_RE.search(auth)
        if not bearer_match:
            continue
        token = bearer_match.group(1)
        source_found = False

        for j in range(i):
            src = requests[j]
            in_body = bool(src.response_body) and token in src.response_body
            in_headers = any(token in v for v in (src.response_headers or {}).values())
            if not (in_body or in_headers):
                continue
            key = (j, token)
            if key not in recorded:
                recorded.add(key)
                all_uses = [
                    k for k in range(j + 1, len(requests))
                    if token in header_get(requests[k].headers, "Authorization")
                ]
                cv = CorrelatedValue(
                    var_name="C_BEARER_TOKEN",
                    source_request_index=j,
                    extract_from="body" if in_body else "header",
                    extract_key=_find_token_key(src.response_body or "", token),
                    extract_value=token,
                    used_in_requests=all_uses,
                    detection_pass="pass1_bearer",
                    reason="Bearer token used in Authorization header",
                )
                found.append(_with_extractor(cv, infer_extractor(src, token)))
                _emit_jwt_claims(token, j)
            source_found = True
            break

        if not source_found:
            # Token response body not captured — fall back to the OAuth token endpoint
            for j in range(i):
                src = requests[j]
                if src.method == "POST" and _TOKEN_ENDPOINT_RE.search(src.url):
                    key = (j, "access_token")
                    if key not in recorded:
                        recorded.add(key)
                        bearer_users = [
                            k for k in range(j + 1, len(requests))
                            if BEARER_RE.search(header_get(requests[k].headers, "Authorization"))
                        ]
                        actual_key = _find_token_key(src.response_body or "", token)
                        found.append(CorrelatedValue(
                            var_name="C_BEARER_TOKEN",
                            source_request_index=j,
                            extract_from="body",
                            extract_key=actual_key,
                            extract_value=token,
                            used_in_requests=bearer_users,
                            detection_pass="pass1_bearer",
                            reason="Bearer token via OAuth token endpoint (response body not recorded)",
                            extractor="json",
                            extractor_config={"path": f"$.{actual_key}"},
                        ))
                        _emit_jwt_claims(token, j)
                    break

    # ── Loop 2: Raw JWT body scan ─────────────────────────────────────────────
    # Keys use the full token: JWTs signed with the same algorithm share their first
    # ~20 characters, so a prefix key would drop the second token of a response.
    for i, r in enumerate(requests):
        if not r.response_body:
            continue
        for jwt in JWT_RE.findall(r.response_body):
            key = (i, jwt)
            if key not in recorded:
                recorded.add(key)
                cv = CorrelatedValue(
                    var_name=_make_var(f"jwt_{i}"),
                    source_request_index=i,
                    extract_from="body",
                    extract_key="(jwt)",
                    extract_value=jwt,
                    used_in_requests=[],
                    detection_pass="pass1_jwt",
                    reason="JWT token found in response body",
                )
                found.append(_with_extractor(cv, infer_extractor(r, jwt)))
                _emit_jwt_claims(jwt, i)

    return found


# ---------------------------------------------------------------------------
# Pass 2: JSON response keys
# ---------------------------------------------------------------------------

def _pass2_json_keys(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Scan all JSON response bodies for dynamic-looking values."""
    found = []
    for i, r in enumerate(requests):
        parsed = _safe_json(r.response_body)
        if parsed is None:
            continue
        for path, leaf in json_leaves(parsed):
            if isinstance(leaf, bool):
                continue
            key = dotted_key(path)
            value = scalar_text(leaf)
            if not _is_dynamic(value, key):
                continue
            jp, note = smart_jsonpath(parsed, path)
            found.append(CorrelatedValue(
                var_name=_make_var(key),   # full path for uniqueness, not just last segment
                source_request_index=i,
                extract_from="body",
                extract_key=key,
                extract_value=value,
                used_in_requests=[],
                detection_pass="pass2_json",
                reason=f"Dynamic JSON field: {key}" + (f" ({note})" if note else ""),
                extractor="json",
                extractor_config={"path": jp, "note": note},
            ))
    return found


# ---------------------------------------------------------------------------
# Pass 2b: UUIDs and numeric IDs in URL paths
# ---------------------------------------------------------------------------

def _pass_url_path_ids(requests: list[HarRequest], resp: TextIndex | None = None) -> list[CorrelatedValue]:
    """
    Extract UUIDs and long numeric IDs from URL path segments, then find
    which earlier response they came from.
    """
    found: list[CorrelatedValue] = []
    seen_values: set[str] = set()
    resp = resp if resp is not None else _responses(requests)

    for i, r in enumerate(requests):
        path = urlparse(r.url).path

        candidates: list[tuple[str, str]] = []
        for uuid in UUID_IN_PATH_RE.findall(path):
            candidates.append((uuid, "url_uuid"))
        for num_id in NUM_ID_IN_PATH_RE.findall(path):
            candidates.append((num_id, "url_id"))

        for val, label_hint in candidates:
            if val.lower() in seen_values:
                continue
            sources = sorted(set().union(*(resp.docs(v, 0, i) for v in {val, val.lower(), val.upper()})))
            for j in sources:
                src = requests[j]
                spec = None
                for v in (val, val.lower(), val.upper()):
                    spec = infer_extractor(src, v)
                    if spec:
                        break
                if not spec:
                    continue
                seen_values.add(val.lower())
                cv = CorrelatedValue(
                    var_name=_make_var(f"{label_hint}_{val[:8].replace('-', '_')}"),
                    source_request_index=j,
                    extract_from=spec["extract_from"],
                    extract_key=spec["extract_key"],
                    extract_value=val,
                    used_in_requests=[i],
                    detection_pass="pass_url_id",
                    reason=f"Dynamic value in URL path: {val[:40]}",
                )
                found.append(_with_extractor(cv, spec))
                break
    return found


# ---------------------------------------------------------------------------
# Pass 3: Response headers, redirects and cookie → header tokens
# ---------------------------------------------------------------------------

def _pass3_headers_cookies(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Extract dynamic values from response headers.
    Cookies are intentionally skipped — the JMeter Cookie Manager handles
    all cookie state automatically via Set-Cookie / Cookie headers.
    """
    found = []
    skip_headers = {
        "content-type", "content-length", "date", "server",
        "cache-control", "expires", "pragma", "connection",
        "transfer-encoding", "vary", "access-control-allow-origin",
        "set-cookie", "location", "content-security-policy", "strict-transport-security",
        "last-modified", "age", "alt-svc", "report-to", "nel",
    }
    for i, r in enumerate(requests):
        for name, value in r.response_headers.items():
            if name.lower() in skip_headers or name.startswith(":"):
                continue
            if _is_dynamic(value, name):
                found.append(CorrelatedValue(
                    var_name=_make_var(name),
                    source_request_index=i,
                    extract_from="header",
                    extract_key=name,
                    extract_value=value,
                    used_in_requests=[],
                    detection_pass="pass3_header",
                    reason=f"Dynamic response header: {name}",
                    extractor="header",
                    extractor_config={"name": name},
                ))
    return found


def _pass3_redirect_params(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """
    Parameters inside Location redirects — OAuth / OIDC authorization codes,
    state, session_state, SSO tickets — which flow into the next request.
    """
    found = []
    for i, r in enumerate(requests):
        loc = header_get(r.response_headers or {}, "Location")
        if not loc:
            continue
        parsed = urlparse(loc)
        params = parse_qsl(parsed.query) + parse_qsl(parsed.fragment)
        for name, value in params:
            if not _is_dynamic(value, name) and name not in ("code", "state", "ticket"):
                continue
            if len(value) < 4:
                continue
            regex = r"[?&#]" + jregex_escape(name) + r"=([^&#\s]+)"
            found.append(CorrelatedValue(
                var_name=_make_var(f"redirect_{name}"),
                source_request_index=i,
                extract_from="header",
                extract_key=f"Location ?{name}=",
                extract_value=value,
                used_in_requests=[],
                detection_pass="pass3_redirect",
                reason=f"'{name}' parameter passed through a redirect",
                extractor="header_regex",
                extractor_config={"regex": regex, "header": "Location", "url_decode": True},
            ))
    return found


def _response_cookies(r: HarRequest) -> list[tuple[str, str]]:
    """Cookies set by a response (from the HAR cookies list, else Set-Cookie headers)."""
    raw_res = (r.raw or {}).get("response", {}) if isinstance(r.raw, dict) else {}
    cookies = [(c.get("name", ""), c.get("value", "")) for c in raw_res.get("cookies", []) if c.get("name")]
    if not cookies:
        headers = raw_res.get("headers") or [
            {"name": k, "value": v} for k, v in (r.response_headers or {}).items()
        ]
        for h in headers:
            if h.get("name", "").lower() == "set-cookie":
                for line in h.get("value", "").split("\n"):
                    first = line.split(";", 1)[0]
                    if "=" in first:
                        n, v = first.split("=", 1)
                        cookies.append((n.strip(), v.strip()))
    return cookies


def _pass3_cookie_to_header(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """
    Double-submit CSRF pattern (Angular, Laravel, Spring): a cookie such as
    XSRF-TOKEN is copied by JavaScript into a request header (X-XSRF-TOKEN).
    The Cookie Manager sends the cookie, but the header must be correlated.
    """
    found = []
    seen: set[str] = set()
    for i, r in enumerate(requests):
        for cname, cval in _response_cookies(r):
            if len(cval) < 8 or cval in seen:
                continue
            decoded = unquote(cval)
            uses = []
            for k in range(i + 1, len(requests)):
                for hname, hval in correlatable_header_items(requests[k].headers):
                    if hname.lower() == "authorization":
                        continue
                    if hval == cval or hval == decoded:
                        uses.append(k)
                        break
            if not uses:
                continue
            seen.add(cval)
            found.append(CorrelatedValue(
                var_name=_make_var(cname),
                source_request_index=i,
                extract_from="header",
                extract_key=f"Set-Cookie {cname}",
                extract_value=decoded if decoded != cval else cval,
                used_in_requests=uses,
                detection_pass="pass3_cookie_header",
                reason=f"Cookie '{cname}' is echoed in a request header (double-submit CSRF)",
                extractor="cookie_header",
                extractor_config={"cookie": cname, "url_decode": decoded != cval},
            ))
    return found


# ---------------------------------------------------------------------------
# Pass 4: Plain text / XML patterns and HTML
# ---------------------------------------------------------------------------

_PLAIN_PATTERNS = {
    "token": re.compile(r'token["\']?\s*[:=]\s*["\']([^"\']{8,})["\']'),
    "access_token": re.compile(r'access_token["\']?\s*[:=]\s*["\']([^"\']{8,})["\']'),
    "refresh_token": re.compile(r'refresh_token["\']?\s*[:=]\s*["\']([^"\']{8,})["\']'),
    "session_id": re.compile(r'session(?:Id|_id)["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', re.IGNORECASE),
    "csrf": re.compile(r'(?:csrf|xsrf)[\w-]*["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', re.IGNORECASE),
    "nonce": re.compile(r'nonce["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', re.IGNORECASE),
}


def _pass4_plain_text(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """Extract tokens from non-JSON responses (JSONP, JS, XML) using regex patterns."""
    # Each pattern starts with its keyword (fast literal scan) and only runs
    # when the keyword occurs in the body at all.
    patterns = [
        ("token", ("token",), _PLAIN_PATTERNS["token"]),
        ("access_token", ("access_token",), _PLAIN_PATTERNS["access_token"]),
        ("refresh_token", ("refresh_token",), _PLAIN_PATTERNS["refresh_token"]),
        ("session_id", ("sessionid", "session_id"), _PLAIN_PATTERNS["session_id"]),
        ("csrf", ("csrf", "xsrf"), _PLAIN_PATTERNS["csrf"]),
        ("nonce", ("nonce",), _PLAIN_PATTERNS["nonce"]),
    ]
    found = []
    for i, r in enumerate(requests):
        if not r.response_body or _safe_json(r.response_body) is not None:
            continue
        lowered = r.response_body.lower()
        seen: set[str] = set()
        for name, keywords, pat in patterns:
            if not any(k in lowered for k in keywords):
                continue
            for match in pat.finditer(r.response_body):
                value = match.group(1)
                if value in seen:
                    continue
                seen.add(value)
                cv = CorrelatedValue(
                    var_name=_make_var(f"{name}_{i}"),
                    source_request_index=i,
                    extract_from="body",
                    extract_key=name,
                    extract_value=value,
                    used_in_requests=[],
                    detection_pass="pass4_text",
                    reason=f"Token pattern '{name}' found in plain text response",
                )
                found.append(_with_extractor(cv, infer_extractor(r, value)))
    return found


_TOKENISH_NAME_RE = re.compile(r"csrf|xsrf|token|nonce|state|viewstate|validation|session|ticket|key", re.I)


def _pass4_html(requests: list[HarRequest]) -> list[CorrelatedValue]:
    """
    HTML responses: hidden form inputs, token meta tags and query parameters
    inside form actions / links (e.g. Keycloak's session_code & execution).
    """
    found = []
    for i, r in enumerate(requests):
        if not looks_like_html(r.response_body, r.response_mime_type):
            continue
        for tag, attrs in html_elements(r.response_body):
            if tag == "input" and attrs.get("name") and attrs.get("value"):
                is_hidden = attrs.get("type", "").lower() == "hidden"
                name, value = attrs["name"], attrs["value"]
                if not (is_hidden or _TOKENISH_NAME_RE.search(name)):
                    continue
                if len(value) < 4:
                    continue
                sel = f"input[name={name}]" if re.fullmatch(r"[\w.:-]+", name) else f'input[name="{name}"]'
                found.append(CorrelatedValue(
                    var_name=_make_var(name),
                    source_request_index=i,
                    extract_from="body",
                    extract_key=sel,
                    extract_value=value,
                    detection_pass="pass_html",
                    reason=f"Hidden form field '{name}'",
                    extractor="css",
                    extractor_config={"selector": sel, "attr": "value"},
                ))
            elif tag == "meta" and attrs.get("content"):
                ident = attrs.get("name") or attrs.get("property") or ""
                if not ident or not _TOKENISH_NAME_RE.search(ident) or len(attrs["content"]) < 8:
                    continue
                key = "name" if attrs.get("name") else "property"
                sel = f"meta[{key}={ident}]" if re.fullmatch(r"[\w.:-]+", ident) else f'meta[{key}="{ident}"]'
                found.append(CorrelatedValue(
                    var_name=_make_var(ident),
                    source_request_index=i,
                    extract_from="body",
                    extract_key=sel,
                    extract_value=attrs["content"],
                    detection_pass="pass_html",
                    reason=f"Token in <meta {key}=\"{ident}\">",
                    extractor="css",
                    extractor_config={"selector": sel, "attr": "content"},
                ))
            elif tag in ("form", "a") and (attrs.get("action") or attrs.get("href")):
                url = attrs.get("action") or attrs.get("href") or ""
                for pname, pval in parse_qsl(urlparse(url).query):
                    if len(pval) < 6 or not _is_dynamic(pval, pname):
                        continue
                    found.append(CorrelatedValue(
                        var_name=_make_var(pname),
                        source_request_index=i,
                        extract_from="body",
                        extract_key=f"{tag} ?{pname}=",
                        extract_value=pval,
                        detection_pass="pass_html",
                        reason=f"'{pname}' parameter in a {'form action' if tag == 'form' else 'link'}",
                        extractor="regex",
                        extractor_config={"regex": jregex_escape(pname) + r"=([^&\"'\s<>]+)", "match_no": 1},
                    ))
    return found


# ---------------------------------------------------------------------------
# Pass R: Framework rule packs
# ---------------------------------------------------------------------------

def _pass_rules(requests: list[HarRequest], packs: list[dict] | None = None) -> list[CorrelatedValue]:
    from projectstack.correlator.rule_packs import apply_rule, load_packs, rule_extractor
    packs = load_packs() if packs is None else packs
    found = []
    for pack in packs:
        for rule in pack.get("rules", []):
            for i, r in enumerate(requests):
                if not (r.response_body or r.response_headers):
                    continue
                value = apply_rule(rule, r)
                if not value or len(value) < 3:
                    continue
                spec = rule_extractor(rule)
                found.append(CorrelatedValue(
                    var_name=rule["var"],
                    source_request_index=i,
                    extract_from=spec["extract_from"],
                    extract_key=spec["extract_key"],
                    extract_value=value,
                    detection_pass="pass_rule",
                    reason=f"{pack.get('name', pack['id'])}: {rule.get('name', rule['var'])}",
                    extractor=spec["extractor"],
                    extractor_config=spec["config"],
                ))
    return found


# ---------------------------------------------------------------------------
# Pass S: Request scan (reverse search)
# ---------------------------------------------------------------------------

def _pass_request_scan(requests: list[HarRequest], known_values: set[str],
                       resp: TextIndex | None = None) -> list[CorrelatedValue]:
    """
    Start from what each request sends and look backwards for the response that
    produced it — catches values embedded in JavaScript, HTML text or anywhere
    the structured passes do not look.
    """
    found = []
    seen = set(known_values)
    resp = resp if resp is not None else _responses(requests)
    for k, req in enumerate(requests):
        for loc, value in request_values(req):
            if value in seen:
                continue
            key = loc.split(":", 1)[-1]
            if not _is_dynamic(value, key) or (loc == "path" and _is_route_segment(value)):
                continue
            for j in resp.docs(value, 0, k):
                src = requests[j]
                spec = infer_extractor(src, value)
                if not spec:
                    continue
                seen.add(value)
                name = key.split(".")[-1] if not loc.startswith("path") else f"path_{value[:8]}"
                cv = CorrelatedValue(
                    var_name=_make_var(name),
                    source_request_index=j,
                    extract_from=spec["extract_from"],
                    extract_key=spec["extract_key"],
                    extract_value=value,
                    used_in_requests=[k],
                    detection_pass="pass_request_scan",
                    reason=f"Sent as {loc} — first appears in response #{j + 1}",
                )
                found.append(_with_extractor(cv, spec))
                break
    return found


# ---------------------------------------------------------------------------
# Pass 5: Backward inference
# ---------------------------------------------------------------------------

def _pass5_backward_inference(
    requests: list[HarRequest],
    candidates: list[CorrelatedValue],
    hay: "TextIndex | list[str] | None" = None,
) -> None:
    """
    For each candidate value, find which later requests actually use it.
    Checks URL, body and headers — raw, URL-encoded and JSON-escaped — with
    boundaries so short values do not match inside longer ones.
    Updates used_in_requests in-place.
    """
    if hay is None:
        hay = _haystacks(requests)
    elif isinstance(hay, list):
        hay = TextIndex(hay)
    for cv in candidates:
        val = cv.extract_value
        if not val or len(val) < 3:
            continue
        for j in _uses(hay, val, cv.source_request_index):
            if j not in cv.used_in_requests:
                cv.used_in_requests.append(j)
        cv.used_in_requests.sort()


# ---------------------------------------------------------------------------
# Pass C: Client-generated values
# ---------------------------------------------------------------------------

_REQUEST_ID_HEADER_RE = re.compile(r"request-?id|correlation-?id|trace-?id|idempotency|x-amzn-trace|nonce", re.I)


def _recorded_epoch(req: HarRequest) -> float | None:
    try:
        return datetime.fromisoformat(req.started_datetime.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _pass_client_generated(requests: list[HarRequest], known_values: set[str],
                           resp: TextIndex | None = None) -> list[CorrelatedValue]:
    """
    Values the browser generated itself — cache-busting timestamps, request
    IDs, idempotency keys. They are not in any response, so they cannot be
    extracted; they must be regenerated with a JMeter function at runtime.
    """
    by_value: dict[str, dict] = {}
    for k, req in enumerate(requests):
        rec_ts = _recorded_epoch(req)
        for loc, value in request_values(req):
            if value in known_values:
                continue
            kind = None
            if re.fullmatch(r"\d{13}|\d{10}", value) and rec_ts:
                secs = int(value) / (1000 if len(value) == 13 else 1)
                if abs(secs - rec_ts) < 2 * 86400:
                    kind = "time" if len(value) == 13 else "time_s"
            elif UUID_RE.fullmatch(value):
                kind = "uuid"
            if not kind:
                continue
            entry = by_value.setdefault(value, {"kind": kind, "uses": [], "locs": set()})
            if k not in entry["uses"]:
                entry["uses"].append(k)
            entry["locs"].add(loc)

    found = []
    if by_value and resp is None:
        resp = _responses(requests)
    for value, entry in by_value.items():
        first = entry["uses"][0]
        # Anything that appeared in an earlier response is a real correlation, not client-generated
        if resp.docs(value, 0, first, first_only=True):
            continue
        if entry["kind"] == "uuid":
            # A UUID sent in many requests is usually static config (tenant ID, app ID) — only
            # replace per-request IDs and ones sent in request-ID style headers.
            header_hit = any(
                loc.startswith("header:") and _REQUEST_ID_HEADER_RE.search(loc) for loc in entry["locs"]
            )
            if not header_hit and len(entry["uses"]) > 1:
                continue
        label = {"time": "timestamp", "time_s": "timestamp", "uuid": "uuid"}[entry["kind"]]
        found.append(CorrelatedValue(
            var_name=_make_var(f"client_{label}_{first + 1}"),
            source_request_index=-1,
            extract_from="client",
            extract_key=", ".join(sorted(entry["locs"]))[:120],
            extract_value=value,
            used_in_requests=entry["uses"],
            detection_pass="pass_client",
            reason={
                "time": "Client-side timestamp (ms) — regenerated with __time()",
                "time_s": "Client-side timestamp (s) — regenerated with __time(/1000)",
                "uuid": "Client-generated UUID — regenerated with __UUID()",
            }[entry["kind"]],
            extractor="function",
            extractor_config={"function": entry["kind"]},
        ))
    return found


# ---------------------------------------------------------------------------
# Pass 6: AI (batched)
# ---------------------------------------------------------------------------

def _pass6_llm(
    requests: list[HarRequest],
    existing_values: set[str],
    ai_settings: dict,
    strict: bool = False,
) -> list[CorrelatedValue]:
    """Use the configured LLM provider to catch anything the rule-based passes missed."""
    from projectstack.correlator.llm_router import detect_with_provider

    print(f"  Sending {len(requests)} requests to the AI provider...")
    detections = detect_with_provider(requests, ai_settings, strict=strict)

    found = []
    provider = ai_settings.get("ai_provider", "ai")
    for d in detections:
        val = str(d.get("value", ""))
        src_idx = d.get("source_request_index", -1)
        if not isinstance(src_idx, int) or src_idx < 0 or src_idx >= len(requests):
            continue
        src = requests[src_idx]
        # The prompt truncates values — recover the full token from the recorded response
        val = expand_truncated(src.response_body or "", val)
        if not val or val in existing_values or not _is_dynamic(val):
            continue
        spec = infer_extractor(src, val)
        if not spec:
            continue  # value is not in the claimed response — model hallucination
        cv = CorrelatedValue(
            var_name=_make_var(str(d.get("field_name") or f"ai_val_{src_idx}")),
            source_request_index=src_idx,
            extract_from=spec["extract_from"],
            extract_key=spec["extract_key"],
            extract_value=val,
            used_in_requests=[],
            detection_pass=f"pass6_{provider}",
            reason=str(d.get("reason", f"Detected by {provider} AI")),
        )
        found.append(_with_extractor(cv, spec))
    return found


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _pass_rank(cv: CorrelatedValue) -> int:
    base = cv.detection_pass.split(",")[0]
    for i, p in enumerate(_PASS_PRIORITY):
        if base.startswith(p):
            return i
    return len(_PASS_PRIORITY)


def _deduplicate(candidates: list[CorrelatedValue]) -> list[CorrelatedValue]:
    """
    Keep one correlation per value: the earliest source response wins, ties go
    to the most specific detection pass. Usages are merged. Variable name
    collisions are then resolved by appending _2, _3, …
    """
    best: dict[str, CorrelatedValue] = {}
    order: list[str] = []
    for cv in candidates:
        key = cv.extract_value
        cur = best.get(key)
        if cur is None:
            best[key] = cv
            order.append(key)
            continue
        merged_uses = sorted(set(cur.used_in_requests) | set(cv.used_in_requests))
        if (cv.source_request_index, _pass_rank(cv)) < (cur.source_request_index, _pass_rank(cur)):
            best[key] = cv
        winner = best[key]
        winner.used_in_requests = [u for u in merged_uses if u > winner.source_request_index]
        if "confirmed_dynamic" in cur.detection_pass + cv.detection_pass and "confirmed_dynamic" not in winner.detection_pass:
            winner.detection_pass += ",confirmed_dynamic"

    unique = sorted((best[k] for k in order), key=lambda c: (c.source_request_index, _pass_rank(c)))

    name_count: dict[str, int] = {}
    for cv in unique:
        base = cv.var_name
        if base not in name_count:
            name_count[base] = 1
        else:
            name_count[base] += 1
            cv.var_name = f"{base}_{name_count[base]}"

    return unique


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_correlation(
    requests: list[HarRequest],
    use_ai: bool = True,
    ai_settings: dict | None = None,
    # kept for CLI backward compat
    use_claude: bool | None = None,
    rule_packs: list[dict] | None = None,
) -> list[CorrelatedValue]:
    """
    Run all correlation passes and return deduplicated CorrelatedValue list.

    Args:
        requests:    Parsed HAR requests.
        use_ai:      Whether to run Pass 6 (AI detection).
        ai_settings: Provider settings dict (provider name + API keys).
        use_claude:  Deprecated alias for use_ai (kept for CLI compat).
        rule_packs:  Rule packs to apply (default: all installed packs).

    Returns:
        List of CorrelatedValue objects ready for JMX generation.
    """
    if use_claude is not None:
        use_ai = use_claude

    print("Running correlation engine...")
    hay = _haystacks(requests)
    resp = _responses(requests)

    all_candidates: list[CorrelatedValue] = []

    print("  Pass 1: JWT / Bearer tokens")
    all_candidates += _pass1_jwt_bearer(requests, hay)

    print("  Pass 2: JSON response keys")
    all_candidates += _pass2_json_keys(requests)

    print("  Pass 2b: UUID and numeric IDs in URL paths")
    all_candidates += _pass_url_path_ids(requests, resp)

    print("  Pass 3: Response headers, redirects, cookie → header tokens")
    all_candidates += _pass3_headers_cookies(requests)
    all_candidates += _pass3_redirect_params(requests)
    all_candidates += _pass3_cookie_to_header(requests)

    print("  Pass 4: Plain text patterns and HTML forms")
    all_candidates += _pass4_plain_text(requests)
    all_candidates += _pass4_html(requests)

    print("  Pass R: Framework rule packs")
    all_candidates += _pass_rules(requests, rule_packs)

    print("  Pass 5: Backward inference")
    _pass5_backward_inference(requests, all_candidates, hay)
    actionable = [cv for cv in all_candidates if cv.used_in_requests]

    print("  Pass S: Request scan")
    scanned = _pass_request_scan(requests, {cv.extract_value for cv in actionable}, resp)
    _pass5_backward_inference(requests, scanned, hay)
    actionable += scanned

    if use_ai:
        effective_settings = ai_settings or {}
        provider = effective_settings.get("ai_provider", "groq")
        print(f"  Pass 6: AI detection ({provider})")
        try:
            ai_found = _pass6_llm(requests, {cv.extract_value for cv in actionable}, effective_settings)
        except Exception as exc:  # AI is best-effort — never fail the whole correlation
            print(f"  [ai] skipped: {exc}")
            ai_found = []
        _pass5_backward_inference(requests, ai_found, hay)
        actionable += [cv for cv in ai_found if cv.used_in_requests]

    print("  Pass C: Client-generated values")
    actionable += _pass_client_generated(requests, {cv.extract_value for cv in actionable}, resp)

    result = _deduplicate(actionable)
    print(f"Correlation complete — {len(result)} correlated values found.")
    return result


def run_ai_correlation(
    requests: list[HarRequest],
    existing: list[CorrelatedValue],
    ai_settings: dict,
) -> list[CorrelatedValue]:
    """
    AI auto-correlation — a separate step from the rule-based engine. Asks the
    configured LLM for dynamic values that `existing` does not already cover
    and returns only the new correlations, named so they do not collide with
    existing variables. Unlike Pass 6 inside run_correlation, a missing key or
    provider error is raised so the caller can report it.
    """
    hay = _haystacks(requests)
    known = {cv.extract_value for cv in existing if cv.extract_value}
    provider = ai_settings.get("ai_provider", "groq")
    print(f"AI auto-correlation ({provider}) on top of {len(existing)} existing correlations...")
    found = _pass6_llm(requests, known, ai_settings, strict=True)
    _pass5_backward_inference(requests, found, hay)
    added = _deduplicate([cv for cv in found if cv.used_in_requests])

    taken = {cv.var_name for cv in existing}
    for cv in added:
        base, n = cv.var_name, 1
        while cv.var_name in taken:
            n += 1
            cv.var_name = f"{base}_{n}"
        taken.add(cv.var_name)
    print(f"AI auto-correlation complete — {len(added)} new correlated values.")
    return added


# ---------------------------------------------------------------------------
# Public API: HAR comparison-enhanced correlation
# ---------------------------------------------------------------------------

def request_signature(r: HarRequest) -> tuple:
    """
    What makes two recordings' requests "the same step": method, host, the path
    with IDs and session parameters blanked out, and the query parameter names.
    /actions/Catalog.action?viewProduct=&productId=FI-SW-01 and …productId=K9-BD-01
    are the same step; /api/orders/123 and /api/orders/456 are too.
    """
    p = urlparse(r.url)
    segs = []
    for seg in p.path.split("/"):
        seg = seg.split(";", 1)[0]            # ;jsessionid=… matrix parameters
        segs.append(seg if not seg or _is_route_segment(seg) else "*")
    names = tuple(sorted({k for k, _ in parse_qsl(p.query, keep_blank_values=True)}))
    return (r.method, p.netloc, "/".join(segs), names)


def pair_requests(requests1: list[HarRequest], requests2: list[HarRequest]) -> dict[int, int]:
    """
    Match each request of the first recording to the same step in the second,
    keeping order: extra or missing requests (a retry, background polling,
    a different click path) no longer shift everything after them.
    """
    import difflib
    sig1 = [request_signature(r) for r in requests1]
    sig2 = [request_signature(r) for r in requests2]
    pairs: dict[int, int] = {}
    for block in difflib.SequenceMatcher(None, sig1, sig2, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            pairs[block.a + k] = block.b + k
    return pairs


def _looks_secret(cv: CorrelatedValue) -> bool:
    """Tokens and secrets stay on even when both recordings had the same value."""
    from projectstack.correlator.confidence import _TOKEN_KEYWORDS, _entropy
    v = cv.extract_value or ""
    return bool(JWT_RE.fullmatch(v) or _TOKEN_KEYWORDS.search(cv.extract_key or "") or _TOKEN_KEYWORDS.search(cv.var_name)
                or (len(v) >= 16 and _entropy(v) >= 3.5))


def run_correlation_with_comparison(
    requests1: list[HarRequest],
    requests2: list[HarRequest],
    use_ai: bool = False,
    ai_settings: dict | None = None,
) -> tuple[list[CorrelatedValue], set[str], dict]:
    """
    Run correlation on HAR1 and compare it with HAR2, a second recording of the
    same journey, step by step:

      * a correlated value that differs in HAR2 is confirmed dynamic
        (detection_pass gets ",confirmed_dynamic");
      * a value that is identical in HAR2 is probably static (a page name, a
        label) — it gets ",static_in_both" and should be switched off, unless it
        looks like a token or secret (",same_in_both": kept, but flagged — both
        runs may have shared a session);
      * values that changed between the recordings but no pass found are added.

    Returns (final_correlations, confirmed_keys, report). The report has the
    pairing coverage and each value's HAR2 counterpart ({extract_value: value_2}).
    """
    print("=== HAR comparison-enhanced correlation ===")

    print("Correlating HAR1 (full)...")
    corr1 = run_correlation(requests1, use_ai=use_ai, ai_settings=ai_settings)

    print("Correlating HAR2 (rule-based only, for comparison)...")
    corr2 = run_correlation(requests2, use_ai=False)

    pairs = pair_requests(requests1, requests2)
    print(f"  Paired {len(pairs)} of {len(requests1)} requests with the second recording")

    val2_by_key: dict[str, str] = {}
    for cv in corr2:
        if cv.extract_key not in val2_by_key:
            val2_by_key[cv.extract_key] = cv.extract_value

    confirmed_keys: set[str] = set()
    values_2: dict[str, str] = {}
    same: set[str] = set()
    for cv in corr1:
        v2 = None
        baseline = cv.extract_value
        j = pairs.get(cv.source_request_index)
        if j is not None and cv.extractor and cv.extractor != "function":
            # Compare what the extractor returns in each recording — on a page with
            # several matching fields it may not pick the exact field that was sent
            r1, r2 = requests1[cv.source_request_index], requests2[j]
            v2 = apply_extractor(cv.extractor, cv.extractor_config or {}, r2.response_body, r2.response_headers) or None
            v1 = apply_extractor(cv.extractor, cv.extractor_config or {}, r1.response_body, r1.response_headers)
            if v2 is not None and v1:
                baseline = v1
        if v2 is None:
            v2 = val2_by_key.get(cv.extract_key)
        if v2 is None:
            continue
        values_2[cv.extract_value] = v2
        if v2 != baseline:
            confirmed_keys.add(cv.extract_key)
        elif cv.extractor != "function":
            same.add(cv.extract_value)

    additional: list[CorrelatedValue] = []
    existing_keys = {cv.extract_key for cv in corr1}
    existing_values = {cv.extract_value for cv in corr1}
    hay1 = _haystacks(requests1)

    SKIP_RESPONSE_HEADERS = {"content-type", "date", "server", "content-length", "set-cookie",
                             "expires", "last-modified", "age"}

    for i, j in sorted(pairs.items()):
        r1 = requests1[i]
        r2 = requests2[j]

        # Compare JSON response bodies
        parsed1 = _safe_json(r1.response_body)
        parsed2 = _safe_json(r2.response_body)
        if parsed1 is not None and parsed2 is not None:
            flat1 = _flatten_json(parsed1)
            flat2 = _flatten_json(parsed2)
            for key, v1 in flat1.items():
                v2 = flat2.get(key)
                if v2 is None or v1 == v2 or not _is_dynamic(v1, key):
                    continue
                if key in existing_keys:
                    confirmed_keys.add(key)
                elif v1 not in existing_values:
                    uses = _uses(hay1, v1, i)
                    spec = infer_extractor(r1, v1)
                    if uses and spec:
                        cv = CorrelatedValue(
                            var_name=_make_var(key),
                            source_request_index=i,
                            extract_from="body",
                            extract_key=key,
                            extract_value=v1,
                            used_in_requests=uses,
                            detection_pass="pass_comparison,confirmed_dynamic",
                            reason="Confirmed dynamic by HAR comparison",
                        )
                        additional.append(_with_extractor(cv, spec))
                        values_2[v1] = v2
                        existing_keys.add(key)
                        existing_values.add(v1)

        # Compare request Authorization headers
        auth1 = header_get(r1.headers, "Authorization")
        auth2 = header_get(r2.headers, "Authorization")
        if auth1 and auth2 and auth1 != auth2:
            for cv in corr1:
                if cv.extract_value and cv.extract_value in auth1:
                    confirmed_keys.add(cv.extract_key)
                    same.discard(cv.extract_value)
                    break

        # Compare response headers (skip static ones)
        for hname, hval1 in r1.response_headers.items():
            if hname.lower() in SKIP_RESPONSE_HEADERS:
                continue
            hval2 = header_get(r2.response_headers, hname, None)
            if hval2 is None or hval1 == hval2 or not _is_dynamic(hval1, hname):
                continue
            if hname in existing_keys:
                confirmed_keys.add(hname)
            elif hval1 not in existing_values:
                uses = _uses(hay1, hval1, i)
                if uses:
                    additional.append(CorrelatedValue(
                        var_name=_make_var(hname),
                        source_request_index=i,
                        extract_from="header",
                        extract_key=hname,
                        extract_value=hval1,
                        used_in_requests=uses,
                        detection_pass="pass_comparison,confirmed_dynamic",
                        reason="Confirmed dynamic by HAR comparison",
                        extractor="header",
                        extractor_config={"name": hname},
                    ))
                    values_2[hval1] = hval2
                    existing_keys.add(hname)
                    existing_values.add(hval1)

    final_correlations = _deduplicate(corr1 + additional)

    static_count = 0
    for cv in final_correlations:
        if cv.extract_key in confirmed_keys and cv.extract_value not in same:
            if "confirmed_dynamic" not in cv.detection_pass:
                cv.detection_pass += ",confirmed_dynamic"
        elif cv.extract_value in same and "confirmed_dynamic" not in cv.detection_pass:
            if _looks_secret(cv):
                cv.detection_pass += ",same_in_both"
                cv.reason = (cv.reason + " — " if cv.reason else "") + \
                    "same value in both recordings; kept because it looks like a token (were both runs in one session?)"
            else:
                cv.detection_pass += ",static_in_both"
                cv.reason = (cv.reason + " — " if cv.reason else "") + \
                    "same value in both recordings, so probably static"
                static_count += 1

    report = {
        "paired": len(pairs),
        "total": len(requests1),
        "second_total": len(requests2),
        "values_2": values_2,
        "static_in_both": static_count,
    }
    print(
        f"HAR comparison complete — {len(final_correlations)} correlated values "
        f"({len(confirmed_keys)} confirmed dynamic, {static_count} same in both recordings)."
    )
    return final_correlations, confirmed_keys, report


# ---------------------------------------------------------------------------
# Serialisation helpers (API / sessions)
# ---------------------------------------------------------------------------

def cv_to_dict(cv: CorrelatedValue) -> dict:
    from projectstack.correlator.confidence import label, score
    s = score(cv)
    return {
        "var_name": cv.var_name,
        "source_request_index": cv.source_request_index,
        "extract_from": cv.extract_from,
        "extract_key": cv.extract_key,
        "extract_value": cv.extract_value or "",
        "used_in_requests": list(cv.used_in_requests),
        "detection_pass": cv.detection_pass,
        "reason": cv.reason,
        "extractor": getattr(cv, "extractor", "") or "",
        "extractor_config": getattr(cv, "extractor_config", None) or {},
        "confidence_score": s,
        "confidence_label": label(s),
        "is_confirmed_dynamic": "confirmed_dynamic" in (cv.detection_pass or ""),
    }


def cv_from_dict(d: dict) -> CorrelatedValue:
    return CorrelatedValue(
        var_name=str(d.get("var_name") or "C_VAR"),
        source_request_index=int(d.get("source_request_index", -1)),
        extract_from=str(d.get("extract_from") or "body"),
        extract_key=str(d.get("extract_key") or ""),
        extract_value=str(d.get("extract_value") or ""),
        used_in_requests=[int(x) for x in (d.get("used_in_requests") or []) if isinstance(x, (int, float))],
        detection_pass=str(d.get("detection_pass") or ""),
        reason=str(d.get("reason") or ""),
        extractor=str(d.get("extractor") or ""),
        extractor_config=dict(d.get("extractor_config") or {}) or None,
    )
