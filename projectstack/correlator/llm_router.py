"""
LLM Router — routes AI-powered dynamic value detection to the configured provider.

Supported providers:
  groq    — Groq (Llama 3.3 70B)   — free tier, requires Groq API key
  gemini  — Google Gemini Flash     — free tier, requires Google AI Studio key
  ollama  — Ollama (local)          — free, requires local Ollama installation
  claude  — Anthropic Claude        — paid, requires Anthropic API key
  openai  — OpenAI GPT-4o-mini      — paid, requires OpenAI API key

Large HAR files are automatically split into overlapping batches so every
request is analysed regardless of total size.
"""

import json
import re
import threading
import time
from typing import Callable

from projectstack.correlator.matching import header_get

# ---------------------------------------------------------------------------
# Provider registry (also consumed by the frontend via /api/providers)
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict] = {
    "groq": {
        "label": "Groq",
        "subtitle": "Llama 3.3 70B",
        "tier": "free",
        "description": "Free tier · ~14,400 tokens/min. Get a free key at console.groq.com",
        "key_field": "groq_api_key",
        "key_placeholder": "gsk_...",
        "model": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "label": "Gemini",
        "subtitle": "Flash",
        "tier": "free",
        "description": "Free tier · generous limits. Get a free key at aistudio.google.com",
        "key_field": "gemini_api_key",
        "key_placeholder": "AIzaSy...",
        "model": "gemini-flash-latest",
    },
    "ollama": {
        "label": "Ollama",
        "subtitle": "gemma4 (local)",
        "tier": "free",
        "description": "Runs locally, completely free. Install from ollama.com then run: ollama pull gemma4",
        "key_field": None,
        "key_placeholder": None,
        "model": "gemma4",
    },
    "claude": {
        "label": "Claude",
        "subtitle": "Opus 5.5",
        "tier": "paid",
        "description": "Best quality. Requires credits at console.anthropic.com",
        "key_field": "anthropic_api_key",
        "key_placeholder": "sk-ant-...",
        "model": "claude-opus-5-5",
    },
    "openai": {
        "label": "OpenAI",
        "subtitle": "GPT-4o-mini",
        "tier": "paid",
        "description": "High quality, low cost. Get key at platform.openai.com",
        "key_field": "openai_api_key",
        "key_placeholder": "sk-...",
        "model": "gpt-4o-mini",
    },
}

# ---------------------------------------------------------------------------
# Batch processing helpers
# ---------------------------------------------------------------------------

_CLOUD_BATCH_SIZE = 60   # cloud providers have large context windows
_OLLAMA_BATCH_SIZE = 30  # local models have smaller context windows
_BATCH_OVERLAP = 10      # overlap so cross-boundary correlations are caught


def _build_traffic_summary(har_requests: list, offset: int = 0) -> str:
    """Compact traffic summary sent to the LLM.

    offset — index of the first request in this batch within the full HAR,
    so the model emits globally-correct source_request_index values.
    """
    n = len(har_requests)
    req_chars = 200 if n > 80 else 300
    res_chars = 250 if n > 80 else 400
    lines = []
    for i, r in enumerate(har_requests):
        auth = header_get(r.headers, "Authorization")[:80]
        req_body = (r.body or "")[:req_chars]
        res_body = (r.response_body or "")[:res_chars]
        lines.append(
            f"[{i + offset}] {r.method} {r.url}\n"
            f"     Auth: {auth}\n"
            f"     ReqBody: {req_body}\n"
            f"     ResBody: {res_body}\n"
        )
    return "\n".join(lines)


def _build_prompt(har_requests: list, offset: int = 0) -> str:
    from projectstack.llm.prompts import load_prompt
    return load_prompt("correlation_detect").render(traffic=_build_traffic_summary(har_requests, offset))[1]


def _run_batched(har_requests: list, call_one_fn: Callable, batch_size: int) -> list[dict]:
    """Call call_one_fn on overlapping windows of har_requests and merge results."""
    n = len(har_requests)
    if n <= batch_size:
        return call_one_fn(har_requests, 0)  # type: ignore[return-value]

    merged: list[dict] = []
    seen: set[tuple] = set()
    step = batch_size - _BATCH_OVERLAP

    for off in range(0, n, step):
        batch = har_requests[off : off + batch_size]  # type: ignore[index]
        raw: list[dict] = list(call_one_fn(batch, off) or [])  # type: ignore[arg-type]
        for item in raw:
            snippet: str = item.get("value") or ""  # type: ignore[assignment]
            key = (item.get("source_request_index"), item.get("field_name"), snippet[:40])  # type: ignore[index]
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _parse_json_array(text: str) -> list[dict]:
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


# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------

def detect_with_provider(har_requests: list, settings: dict, strict: bool = False) -> list[dict]:
    """
    Route AI dynamic value detection to the configured provider (settings["ai_provider"],
    with the model from llm_tasks.correlation or llm_models when set).

    Returns list of dicts with keys:
      source_request_index, field_name, location, value,
      used_in_request_indices, reason.
    Returns empty list on any error or missing configuration, unless `strict`
    is set: then a missing key raises ValueError and a provider error RuntimeError.
    """
    from projectstack.llm import LLMConfigError, provider_named
    from projectstack.llm.registry import resolve

    if not har_requests:
        return []

    provider = settings.get("ai_provider", "groq")
    info = PROVIDERS.get(provider)
    if info is None:
        if strict:
            raise ValueError(f"Unknown AI provider '{provider}' — choose one in Settings")
        print(f"  [ai] Unknown provider '{provider}' — skipping")
        return []
    label = info["label"]
    print(f"  [ai] Provider: {label}")
    task_provider, task_model = resolve("correlation", settings)
    try:
        llm = provider_named(provider, settings, task_model if task_provider == provider else None)
    except LLMConfigError as exc:
        return _no_key(f"{label}: {exc}", strict)

    max_tokens = 8192 if provider == "gemini" else 4096

    def _one(batch: list, offset: int) -> list[dict]:
        return _parse_json_array(llm.complete("", _build_prompt(batch, offset), max_tokens=max_tokens).text)

    try:
        result = _run_batched(har_requests, _one, _OLLAMA_BATCH_SIZE if provider == "ollama" else _CLOUD_BATCH_SIZE)
        print(f"  [ai] {label}: {len(result)} dynamic values detected")
        return result
    except Exception as exc:
        return _failed(label, exc, strict, exc)


def _no_key(message: str, strict: bool) -> list[dict]:
    if strict:
        raise ValueError(message)
    print(f"  [ai] {message}")
    return []


def _failed(label: str, detail: object, strict: bool, exc: Exception, hint: str = "skipping AI detection") -> list[dict]:
    if strict:
        raise RuntimeError(f"{label}: {detail}") from exc
    print(f"  [ai] {label} WARNING: {detail} — {hint}")
    return []


# ---------------------------------------------------------------------------
# Gemini transport (used by projectstack.llm.providers.local.GeminiProvider)
# ---------------------------------------------------------------------------

# Used only when the model list cannot be fetched. "-latest" aliases survive retirements.
_GEMINI_FALLBACK_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")
_gemini_model_cache: dict[str, tuple[float, list[str]]] = {}
# Per-model thinking setting that the API accepted last time (None = send none)
_gemini_thinking_ok: dict[str, dict | None] = {}
_THINKING_OPTIONS: tuple[dict | None, ...] = ({"thinkingLevel": "low"}, {"thinkingBudget": 0}, None)
# (api_key, model) -> monotonic time until which the model is skipped (out of quota / overloaded)
_gemini_cooldown: dict[tuple[str, str], float] = {}
# The model that answered the last call on this thread, and its token usage (for report provenance)
_gemini_last = threading.local()
_COOLDOWN_SECONDS = {"quota": 900.0, "overloaded": 120.0}


def _gemini_models(api_key: str) -> list[str]:
    """
    Text-generation Flash models this key can use, best first: the -latest alias,
    then numbered Flash models newest first, then the Lite variants. Google retires
    numbered models regularly, so the list is discovered from the API (cached 1 h).
    """
    import requests as http_lib  # noqa: PLC0415

    cached = _gemini_model_cache.get(api_key)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    try:
        resp = http_lib.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                            headers={"x-goog-api-key": api_key}, timeout=(5, 15))
        resp.raise_for_status()
        names = [
            m["name"].split("/", 1)[-1] for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
    except Exception:
        return list(_GEMINI_FALLBACK_MODELS)

    def version(name: str) -> float:
        m = re.search(r"gemini-(\d+(?:\.\d+)?)-", name)
        return float(m.group(1)) if m else 0.0

    text_flash = [
        n for n in names
        if "flash" in n and not re.search(r"tts|image|audio|live|omni|embedding|preview|exp", n)
    ]
    full = sorted((n for n in text_flash if "lite" not in n and n != "gemini-flash-latest"), key=version, reverse=True)
    lite = sorted((n for n in text_flash if "lite" in n and n != "gemini-flash-lite-latest"), key=version, reverse=True)
    ordered = (["gemini-flash-latest"] if "gemini-flash-latest" in names else []) + full[:2] \
        + (["gemini-flash-lite-latest"] if "gemini-flash-lite-latest" in names else []) + lite[:1]
    ordered = ordered or list(_GEMINI_FALLBACK_MODELS)
    _gemini_model_cache[api_key] = (time.time(), ordered)
    return ordered


def _gemini_generate(
    api_key: str,
    prompt: str,
    system_prompt: str | None = None,
    max_output_tokens: int = 8192,
    time_budget: float = 150.0,
    json_mode: bool = False,
    pin_model: str | None = None,
) -> str:
    """
    Call the Gemini REST API directly — avoids the deprecated google-generativeai SDK.

    Thinking is kept to a minimum: the flash models otherwise reason for a minute or
    more before answering, which made report narratives time out. Newer models take
    thinkingLevel, older ones thinkingBudget — whichever the model accepts is used.
    A model that is out of quota, retired or not answering is skipped for the next
    one, and the whole call is bounded by `time_budget` seconds. `json_mode` asks for a
    JSON response; `pin_model` uses that model instead of discovering the Flash models.
    """
    import requests as http_lib  # noqa: PLC0415

    if not api_key:
        raise ValueError("No Gemini API key configured — add one in Settings (free at aistudio.google.com)")

    deadline = time.monotonic() + time_budget
    # Non-streaming answers arrive only once fully generated, so the read timeout
    # scales with the output budget (≈45 s for a short narrative, ≤90 s for big batches).
    read_timeout = min(90.0, 20.0 + max_output_tokens / 40)
    errors: list[str] = []
    quota_hits = 0
    now = time.monotonic()
    all_models = [pin_model] if pin_model else _gemini_models(api_key)
    models = [m for m in all_models if _gemini_cooldown.get((api_key, m), 0) <= now] or all_models
    for model in models:
        options = [_gemini_thinking_ok[model]] if model in _gemini_thinking_ok else list(_THINKING_OPTIONS)
        resp = None
        option_idx, attempt = 0, 0
        while option_idx < len(options) and attempt < 2:
            remaining = deadline - time.monotonic()
            if remaining < 5:
                raise RuntimeError(f"Gemini did not answer within {int(time_budget)}s ({'; '.join(errors) or 'no response'})")
            generation_config: dict = {"maxOutputTokens": max_output_tokens}
            if json_mode:
                generation_config["responseMimeType"] = "application/json"
            if options[option_idx]:
                generation_config["thinkingConfig"] = options[option_idx]
            body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": generation_config}
            if system_prompt:
                body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
            try:
                resp = http_lib.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=(10, min(read_timeout, remaining)),
                )
            except (http_lib.exceptions.Timeout, http_lib.exceptions.ConnectionError) as exc:
                resp = None
                errors.append(f"{model}: {type(exc).__name__}")
                break                           # not answering — try the next model
            if resp.status_code == 400 and options[option_idx] is not None:
                option_idx += 1                 # thinking setting not supported by this model
                continue
            if resp.status_code == 429 and "quota" in resp.text.lower():
                break                           # out of quota — retrying will not help
            if resp.status_code == 429 and attempt == 0:
                attempt += 1                    # short rate limit — one retry
                time.sleep(min(2.0, max(0.0, deadline - time.monotonic() - 5)))
                continue
            break                               # success, or overloaded (503) → next model
        if resp is None:
            continue
        if resp.ok:
            _gemini_thinking_ok[model] = options[option_idx] if option_idx < len(options) else None
            data = resp.json()
            _gemini_last.model, _gemini_last.usage = model, data.get("usageMetadata") or {}
            candidates = data.get("candidates") or []
            if not candidates:
                raise RuntimeError("Gemini returned no candidates (the prompt may have been blocked)")
            parts = candidates[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if text.strip():
                return text
            errors.append(f"{model}: empty answer ({candidates[0].get('finishReason')})")
            continue
        if resp.status_code == 429 and "quota" in resp.text.lower():
            quota_hits += 1
            errors.append(f"{model}: quota exceeded")
            _gemini_cooldown[(api_key, model)] = time.monotonic() + _COOLDOWN_SECONDS["quota"]
            continue
        if resp.status_code == 503:
            _gemini_cooldown[(api_key, model)] = time.monotonic() + _COOLDOWN_SECONDS["overloaded"]
        if resp.status_code in (404, 429, 503):
            errors.append(f"{model}: HTTP {resp.status_code}")
            continue
        try:
            msg = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            msg = resp.text
        raise RuntimeError(f"Gemini API error {resp.status_code}: {msg[:300]}")

    if quota_hits and quota_hits == len(errors):
        raise RuntimeError(
            "Your Gemini API key has used up its quota for every available Flash model. "
            "Wait for the quota to reset (free tier resets daily), add billing in Google AI Studio, "
            "or switch the AI provider in Settings."
        )
    raise RuntimeError(f"Gemini request failed — {'; '.join(errors) or 'no available model'}")


# ---------------------------------------------------------------------------
# Generic single-prompt helper (for non-correlation AI calls)
# ---------------------------------------------------------------------------

def call_llm_prompt(system_prompt: str, user_prompt: str, settings: dict, max_tokens: int = 4096) -> str:
    """
    Send a single system+user prompt to the configured provider (settings["ai_provider"])
    through the shared LLM layer. Returns the text; raises projectstack.llm.LLMError on failure.
    """
    from projectstack.llm import provider_named
    llm = provider_named(settings.get("ai_provider", "groq"), settings)
    return llm.complete(system_prompt, user_prompt, max_tokens=max_tokens).text
