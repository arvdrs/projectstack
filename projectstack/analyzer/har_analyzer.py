"""
HAR analysis utilities.

Provides: protocol detection, auth flow detection, duplicate detection,
rate-limit detection, sensitive data scanning, and full analysis summary.
"""

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from projectstack.parser.har_parser import HarRequest


# ---------------------------------------------------------------------------
# Protocol detection
# ---------------------------------------------------------------------------

def detect_protocol(req: HarRequest) -> str:
    """Classify a request as rest | graphql | soap | grpc."""
    body = req.body or ""
    mime = (req.body_mime_type or "").lower()
    url = req.url.lower()

    # gRPC: application/grpc content-type
    if "application/grpc" in mime:
        return "grpc"

    # GraphQL: /graphql in URL or { query/mutation } in JSON body
    if "/graphql" in url or "graphql" in url:
        return "graphql"
    if "application/json" in mime and body.strip().startswith("{"):
        try:
            data = json.loads(body)
            if isinstance(data, dict) and ("query" in data or "mutation" in data or "operationName" in data):
                return "graphql"
        except Exception:
            pass

    # SOAP: XML body with Envelope element
    if "xml" in mime or "soap" in url or "wsdl" in url:
        if any(tag in body for tag in ("Envelope", "soap:Body", "SOAP-ENV", ":Envelope")):
            return "soap"

    return "rest"


# ---------------------------------------------------------------------------
# Auth flow detection
# ---------------------------------------------------------------------------

_AUTH_URL_PATTERNS = re.compile(
    r"/(?:login|signin|sign-in|auth|token|connect/token|oauth|authorize|session)",
    re.IGNORECASE,
)


def detect_auth_flow(requests: list[HarRequest]) -> dict[str, Any]:
    """Detect login endpoints, Bearer token usage, and API key headers."""
    auth_endpoints: list[dict] = []
    bearer_requests: list[int] = []
    api_key_requests: list[int] = []

    for i, req in enumerate(requests):
        parsed_path = urlparse(req.url).path
        if _AUTH_URL_PATTERNS.search(parsed_path):
            auth_endpoints.append({"index": i, "url": req.url, "method": req.method})

        auth_hdr = req.headers.get("Authorization", req.headers.get("authorization", ""))
        if auth_hdr.lower().startswith("bearer "):
            bearer_requests.append(i)

        if any(k.lower() in ("x-api-key", "x-auth-token", "api-key", "apikey") for k in req.headers):
            api_key_requests.append(i)

    return {
        "has_auth": bool(auth_endpoints or bearer_requests or api_key_requests),
        "auth_endpoints": auth_endpoints[:10],
        "bearer_request_count": len(bearer_requests),
        "api_key_request_count": len(api_key_requests),
    }


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def detect_duplicates(requests: list[HarRequest]) -> list[dict]:
    """Return requests that are exact duplicates (method + URL + body)."""
    seen: dict[str, int] = {}
    duplicates: list[dict] = []
    for i, req in enumerate(requests):
        key = f"{req.method}:{req.url}:{req.body or ''}"
        if key in seen:
            duplicates.append({
                "index": i,
                "duplicate_of": seen[key],
                "method": req.method,
                "url": req.url,
            })
        else:
            seen[key] = i
    return duplicates


# ---------------------------------------------------------------------------
# Rate-limit detection
# ---------------------------------------------------------------------------

def detect_rate_limits(requests: list[HarRequest]) -> list[dict]:
    """Find requests that received HTTP 429 Too Many Requests."""
    findings: list[dict] = []
    for i, req in enumerate(requests):
        if req.status_code == 429:
            retry_after = (
                req.response_headers.get("Retry-After")
                or req.response_headers.get("retry-after")
                or ""
            )
            findings.append({"index": i, "url": req.url, "retry_after": retry_after})
    return findings


# ---------------------------------------------------------------------------
# Sensitive data scanner
# ---------------------------------------------------------------------------

_PII_PATTERNS: dict[str, re.Pattern] = {
    "email":        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I),
    "ssn":          re.compile(r"\b\d{3}[.\-]?\d{2}[.\-]?\d{4}\b"),
    "credit_card":  re.compile(r"\b(?:\d[ \-]?){13,16}\b"),
    "phone":        re.compile(r"\b(?:\+?1[\-.\s]?)?\(?\d{3}\)?[\-.\s]?\d{3}[\-.\s]?\d{4}\b"),
    "aws_key":      re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pors]_[A-Za-z0-9]{36,}"),
    "jwt":          re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    "private_key":  re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I),
}


_SENSITIVE_PARAM_NAMES = re.compile(
    r"(?:^|&)(?:password|passwd|secret|api_key|apikey|token|access_token|client_secret"
    r"|private_key|key|credential|auth|authorization)=([^&\s]+)",
    re.IGNORECASE,
)


def _decode_body(text: str) -> str:
    """URL-decode a string so patterns match encoded payloads."""
    from urllib.parse import unquote_plus
    try:
        return unquote_plus(text)
    except Exception:
        return text


def scan_sensitive_data(requests: list[HarRequest]) -> list[dict]:
    """Scan request/response bodies (including URL-encoded) and URLs for PII and sensitive tokens."""
    findings: list[dict] = []
    seen: set[tuple] = set()  # deduplicate (request_index, field, type)

    for i, req in enumerate(requests):
        raw_body = req.body or ""
        mime = (req.body_mime_type or "").lower()
        # URL-decode form-encoded bodies so regex can match
        decoded_body = _decode_body(raw_body) if "form" in mime or "&" in raw_body else raw_body

        fields = [
            ("url",           req.url),
            ("request_body",  decoded_body),
            ("response_body", _decode_body(req.response_body or "")),
        ]

        # Explicit check for sensitive param names in form-encoded bodies
        for match in _SENSITIVE_PARAM_NAMES.finditer(decoded_body):
            key = (i, "request_body", "sensitive_param")
            if key not in seen:
                seen.add(key)
                raw_match: str = match.group(0)
                sample_str = raw_match if len(raw_match) <= 60 else raw_match[0:60] + "…"
                findings.append({
                    "request_index": i,
                    "url": req.url,
                    "field": "request_body",
                    "type": "sensitive_param",
                    "count": 1,
                    "sample": sample_str,
                })

        for field_name, text in fields:
            if not text:
                continue
            for pii_type, pattern in _PII_PATTERNS.items():
                matches = pattern.findall(text)
                if not matches:
                    continue
                key = (i, field_name, pii_type)
                if key in seen:
                    continue
                seen.add(key)
                # Redact actual values in the sample for credit cards
                sample = matches[0]
                if pii_type == "credit_card":
                    sample = "****-****-****-" + str(sample)[-4:]
                elif pii_type in ("jwt", "bearer_token", "private_key"):
                    sample = sample[:40] + "…"
                findings.append({
                    "request_index": i,
                    "url": req.url,
                    "field": field_name,
                    "type": pii_type,
                    "count": len(matches),
                    "sample": sample[:80],
                })
    return findings


# ---------------------------------------------------------------------------
# Full analysis summary
# ---------------------------------------------------------------------------

def analyze_har(requests: list[HarRequest]) -> dict[str, Any]:
    """
    Return a full analysis dict for the HAR session.

    Keys:
        total, methods, statuses, protocols, domains,
        avg_response_ms, max_response_ms, error_count,
        auth_flow, duplicates, rate_limited, sensitive_findings,
        missing_response_bodies
    """
    methods   = Counter(r.method for r in requests)
    statuses  = Counter(r.status_code for r in requests)
    protocols = Counter(detect_protocol(r) for r in requests)
    domains   = Counter(urlparse(r.url).netloc for r in requests)

    # Response times from raw HAR entry ("time" field in ms)
    times_ms: list[float] = []
    for r in requests:
        t = r.raw.get("time", 0)
        if isinstance(t, (int, float)) and t > 0:
            times_ms.append(float(t))

    avg_ms = round(sum(times_ms) / len(times_ms), 1) if times_ms else 0.0
    max_ms = round(max(times_ms), 1) if times_ms else 0.0

    return {
        "total":             len(requests),
        "methods":           dict(methods.most_common()),
        "statuses":          dict(statuses.most_common()),
        "protocols":         dict(protocols.most_common()),
        "domains":           dict(domains.most_common(10)),
        "avg_response_ms":   avg_ms,
        "max_response_ms":   max_ms,
        "error_count":       sum(1 for r in requests if r.status_code >= 400),
        "auth_flow":         detect_auth_flow(requests),
        "duplicates":        detect_duplicates(requests),
        "rate_limited":      detect_rate_limits(requests),
        "sensitive_findings": scan_sensitive_data(requests),
        "missing_response_bodies": missing_response_bodies(requests),
    }


def missing_response_bodies(requests: list[HarRequest]) -> list[int]:
    """
    Indices of requests whose response body was not captured although it
    should have one (2xx, not 204/HEAD, non-empty according to the HAR).
    Browsers drop bodies for some entries — correlation cannot see values
    inside them, so the user should know before trusting the result.
    """
    missing = []
    for i, r in enumerate(requests):
        if r.response_body or r.method == "HEAD" or not (200 <= r.status_code < 300) or r.status_code == 204:
            continue
        content = (r.raw.get("response") or {}).get("content") or {} if isinstance(r.raw, dict) else {}
        if content.get("size", 1) == 0:
            continue
        missing.append(i)
    return missing
