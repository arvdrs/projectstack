"""
HAR file parser.

Reads a .har file and returns a clean list of Request objects
containing everything the correlation engine and JMX generator need.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    # Raw entry kept for any extra processing
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _headers_to_dict(headers: list[dict]) -> dict[str, str]:
    """Convert HAR header list to a plain dict (last value wins on duplicates)."""
    return {h["name"]: h["value"] for h in headers}


def _safe_text(content: dict) -> str | None:
    """Extract text from a HAR content block, returning None if absent."""
    return content.get("text") or None


def parse_har(path: str | Path) -> list[HarRequest]:
    """
    Parse a HAR file and return a list of HarRequest objects.

    Args:
        path: Path to the .har file.

    Returns:
        Ordered list of HarRequest objects, one per HAR entry.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not valid HAR JSON.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"HAR file not found: {path}")

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

        # --- Request body ---
        post_data = req.get("postData", {})
        body = post_data.get("text") if post_data else None
        body_mime = post_data.get("mimeType") if post_data else None

        # --- Response body ---
        res_content = res.get("content", {})
        response_body = _safe_text(res_content)
        response_mime = res_content.get("mimeType")

        requests.append(
            HarRequest(
                method=req.get("method", "GET").upper(),
                url=req.get("url", ""),
                headers=_headers_to_dict(req.get("headers", [])),
                query_params=req.get("queryString", []),
                body=body,
                body_mime_type=body_mime,
                status_code=res.get("status", 0),
                response_headers=_headers_to_dict(res.get("headers", [])),
                response_body=response_body,
                response_mime_type=response_mime,
                cookies=req.get("cookies", []),
                raw=entry,
            )
        )

    return requests


def summarize(requests: list[HarRequest]) -> None:
    """Print a quick summary of parsed requests to stdout."""
    print(f"Total requests parsed: {len(requests)}")
    for i, r in enumerate(requests, 1):
        body_hint = f"  body={len(r.body)} chars" if r.body else ""
        print(f"  [{i:>3}] {r.method:<7} {r.status_code}  {r.url}{body_hint}")
