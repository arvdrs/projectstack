"""
Ollama client for dynamic value detection.

Sends a request/response pair to the local Mistral model and asks it
to identify dynamic values that need to be correlated (tokens, IDs, etc.).
"""

import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral"

PROMPT_TEMPLATE = """You are a performance testing expert analyzing HTTP traffic.

Examine this HTTP request and response and identify all dynamic values that must be correlated — values that are generated server-side and must be extracted from one response and injected into a later request.

Examples of dynamic values:
- Bearer tokens / JWT tokens
- Session IDs / cookies
- CSRF tokens
- User IDs, order IDs, transaction IDs
- Timestamps used as parameters
- Any random/unique string in a response that appears in a later request

HTTP Request:
  Method: {method}
  URL: {url}
  Headers: {req_headers}
  Body: {req_body}

HTTP Response:
  Status: {status}
  Headers: {res_headers}
  Body: {res_body}

Respond ONLY with a JSON array. Each item must have:
  "key"     : the field name or header name where the value lives
  "value"   : the actual dynamic value (first 80 chars max)
  "location": one of "response_body", "response_header", "cookie"
  "reason"  : one short sentence explaining why it is dynamic

If nothing dynamic is found, return an empty array: []

JSON array:"""


def detect_dynamic_values(
    method: str,
    url: str,
    req_headers: dict,
    req_body: str | None,
    status: int,
    res_headers: dict,
    res_body: str | None,
    timeout: int = 60,
) -> list[dict]:
    """
    Ask Mistral to identify dynamic values in a request/response pair.

    Returns a list of dicts with keys: key, value, location, reason.
    Returns an empty list if Ollama is unavailable or returns unparseable output.
    """
    prompt = PROMPT_TEMPLATE.format(
        method=method,
        url=url,
        req_headers=json.dumps(req_headers, indent=2)[:500],
        req_body=(req_body or "")[:500],
        status=status,
        res_headers=json.dumps(res_headers, indent=2)[:500],
        res_body=(res_body or "")[:1000],
    )

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("  [ollama] WARNING: Ollama not reachable — skipping AI detection")
        return []
    except requests.exceptions.Timeout:
        print("  [ollama] WARNING: Ollama timed out — skipping AI detection")
        return []
    except requests.exceptions.RequestException as exc:
        print(f"  [ollama] WARNING: {exc} — skipping AI detection")
        return []

    raw = response.json().get("response", "")
    return _parse_json_array(raw)


def _parse_json_array(text: str) -> list[dict]:
    """Extract the first JSON array found in a string."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        result = json.loads(text[start : end + 1])
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    return []
