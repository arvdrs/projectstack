"""
HAR file parser.

Reads a .har file and returns a clean list of HarRequest objects
containing everything the correlation engine and JMX generator need.

Noise requests (analytics, tracking, static assets, WebSocket) are
filtered out automatically.  Each request is classified as one of:
  token_request  – POST to /token or /connect/token
  api_json       – Content-Type: application/json
  form_post      – Content-Type: application/x-www-form-urlencoded
  static_resource – anything else (kept only if filtering is disabled)
"""

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse


# ---------------------------------------------------------------------------
# Noise-filtering constants
# ---------------------------------------------------------------------------

NOISE_DOMAINS = [
    "dc.services.visualstudio.com",
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.com/tr",
    "analytics.",
    "hotjar.com",
    "segment.io",
    "mixpanel.com",
    "doubleclick.net",
    "adservice.google.",
    "connect.facebook.net",
]

STATIC_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif",
    ".woff", ".woff2", ".ico", ".svg", ".ttf", ".eot",
    ".map", ".webp", ".avif",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class HarRequest:
    """A single HTTP request/response pair extracted from a HAR entry."""

    method: str
    url: str
    headers: dict[str, str]
    query_params: list[dict[str, str]]
    body: str | None
    body_mime_type: str | None
    status_code: int
    response_headers: dict[str, str]
    response_body: str | None
    response_mime_type: str | None
    cookies: list[dict[str, str]]
    request_type: str = "api_json"     # token_request | api_json | form_post | static_resource
    started_datetime: str = ""          # ISO 8601 from HAR entry
    # Raw entry kept for any extra processing
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers_to_dict(headers: list[dict]) -> dict[str, str]:
    """Convert HAR header list to a plain dict (last value wins on duplicates)."""
    return {h["name"]: h["value"] for h in headers}


def _safe_text(content: dict) -> str | None:
    """Extract text from a HAR content block, decoding base64 if needed."""
    text = content.get("text")
    if not text:
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace") or None
        except Exception:
            pass
    return text


def _form_encode(value: str) -> str:
    """Form-encode a HAR param value unless the exporter already encoded it."""
    return value if "%" in value else quote_plus(value)


def _is_noise(url: str, headers: dict[str, str]) -> bool:
    """Return True if this request should be filtered out."""
    # WebSocket upgrades (header names are lowercase in HTTP/2 recordings)
    upgrade = next((v for k, v in headers.items() if k.lower() == "upgrade"), "")
    if upgrade.lower() == "websocket":
        return True

    for domain in NOISE_DOMAINS:
        if domain in url:
            return True

    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path.lower())
    if ext in STATIC_EXTENSIONS:
        return True

    return False


def _classify(method: str, url: str, body_mime: str | None) -> str:
    """Classify a request into one of the four request types."""
    url_lower = url.lower()
    mime = (body_mime or "").lower()

    if method == "POST" and (
        "/token" in url_lower or "/connect/token" in url_lower
    ):
        return "token_request"

    if "application/json" in mime:
        return "api_json"

    if "x-www-form-urlencoded" in mime:
        return "form_post"

    return "api_json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_domains(path: str | Path) -> list[str]:
    """Return unique non-noise API domains from a HAR file, ordered by request frequency."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["log"]["entries"]
    except Exception:
        return []

    counts: dict[str, int] = {}
    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        headers = _headers_to_dict(req.get("headers", []))
        if _is_noise(url, headers):
            continue
        parsed = urlparse(url)
        host = parsed.netloc
        if host:
            counts[host] = counts.get(host, 0) + 1

    return sorted(counts.keys(), key=lambda h: -counts[h])


def parse_har(path: str | Path, filter_noise: bool = True, base_url: str | None = None) -> list[HarRequest]:
    """
    Parse a HAR file and return a list of HarRequest objects.

    Args:
        path:          Path to the .har file.
        filter_noise:  When True (default), discard analytics/tracking/static requests.
        base_url:      When provided, only include requests whose host matches this domain.

    Returns:
        Ordered list of HarRequest objects, one per non-noise HAR entry.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError:        If the file is not valid HAR JSON.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"HAR file not found: {path}")

    # Normalise base_url to just the netloc for comparison
    _base_host: str | None = None
    if base_url:
        _p = urlparse(base_url if "://" in base_url else f"https://{base_url}")
        _base_host = _p.netloc or base_url.split("/")[0]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in HAR file: {exc}") from exc

    try:
        entries = data["log"]["entries"]
    except KeyError as exc:
        raise ValueError(f"Not a valid HAR file — missing key: {exc}") from exc

    requests: list[HarRequest] = []

    for entry in entries:
        req = entry.get("request", {})
        res = entry.get("response", {})

        method = req.get("method", "GET").upper()
        url = req.get("url", "")
        req_headers = _headers_to_dict(req.get("headers", []))

        # Filter noise early so we don't waste time parsing bodies
        if filter_noise and _is_noise(url, req_headers):
            continue

        # Filter by base URL domain if provided
        if _base_host:
            parsed_url = urlparse(url)
            if parsed_url.netloc != _base_host:
                continue

        # --- Request body ---
        post_data = req.get("postData", {})
        body = post_data.get("text") if post_data else None
        body_mime = post_data.get("mimeType") if post_data else None
        if not body and post_data and post_data.get("params"):
            # Some exporters only record form fields as params — rebuild the body
            body = "&".join(
                f"{_form_encode(p.get('name', ''))}={_form_encode(p.get('value', ''))}"
                for p in post_data["params"]
            )
            body_mime = body_mime or "application/x-www-form-urlencoded"

        # --- Response body ---
        res_content = res.get("content", {})
        response_body = _safe_text(res_content)
        response_mime = res_content.get("mimeType")

        request_type = _classify(method, url, body_mime)

        requests.append(
            HarRequest(
                method=method,
                url=url,
                headers=req_headers,
                query_params=req.get("queryString", []),
                body=body,
                body_mime_type=body_mime,
                status_code=res.get("status", 0),
                response_headers=_headers_to_dict(res.get("headers", [])),
                response_body=response_body,
                response_mime_type=response_mime,
                cookies=req.get("cookies", []),
                request_type=request_type,
                started_datetime=entry.get("startedDateTime", ""),
                raw=entry,
            )
        )

    return requests


def summarize(requests: list[HarRequest]) -> None:
    """Print a quick summary of parsed requests to stdout."""
    print(f"Total requests parsed: {len(requests)}")
    for i, r in enumerate(requests, 1):
        body_hint = f"  body={len(r.body)} chars" if r.body else ""
        print(f"  [{i:>3}] {r.method:<7} {r.status_code}  [{r.request_type}]  {r.url}{body_hint}")
