"""
Which provider and model each task uses.

Settings (JSON strings in ~/.projectstack_settings.json):

  llm_tasks   {"correlation": "ollama/mistral", "report": "claude", "ask": "claude/claude-opus-5-5",
               "screenshots": "ollama/gemma4"}
  llm_models  {"claude": "claude-opus-5-5", "groq": "openai/gpt-oss-120b"}   per-provider default model

Without llm_tasks: correlation uses the provider chosen in the web app (ai_provider);
report, ask and screenshots use Claude when an Anthropic key is available, otherwise
Gemini when a Gemini key is set, otherwise ai_provider.

Reading screenshots needs a model that sees images: vision_choices() lists the ones
this machine can use (the cloud providers with a key, and Ollama models that report
the "vision" capability).
"""

from __future__ import annotations

import json
import os

from projectstack.llm.base import LLMConfigError, Provider

TASKS = ("correlation", "report", "ask", "screenshots")


def _classes() -> dict[str, type[Provider]]:
    from projectstack.llm.providers.claude import ClaudeProvider
    from projectstack.llm.providers.local import GeminiProvider, OllamaProvider
    from projectstack.llm.providers.openai_compat import GroqProvider, OpenAIProvider
    return {"claude": ClaudeProvider, "openai": OpenAIProvider, "groq": GroqProvider,
            "gemini": GeminiProvider, "ollama": OllamaProvider}


def _json_setting(settings: dict[str, str], key: str) -> dict:
    raw = settings.get(key) or ""
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as exc:
        raise LLMConfigError(f"Setting {key} is not valid JSON: {exc}") from exc
    return value if isinstance(value, dict) else {}


def resolve(task: str, settings: dict[str, str]) -> tuple[str, str | None]:
    """(provider name, model or None) for a task."""
    spec = _json_setting(settings, "llm_tasks").get(task)
    if spec:
        name, _, model = str(spec).partition("/")
        return name.strip(), (model.strip() or None)
    if task == "correlation":
        return settings.get("ai_provider") or "ollama", None
    if settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"):
        return "claude", None
    if settings.get("gemini_api_key"):
        return "gemini", None
    return settings.get("ai_provider") or "ollama", None


def provider_named(name: str, settings: dict[str, str], model: str | None = None) -> Provider:
    classes = _classes()
    if name not in classes:
        raise LLMConfigError(f"Unknown AI provider {name!r} — use one of {', '.join(classes)}")
    model = model or _json_setting(settings, "llm_models").get(name)
    return classes[name](settings, model)


def provider_for_task(task: str, settings: dict[str, str], override: str | None = None) -> Provider:
    """The provider for a task; `override` ("claude" or "groq/openai/gpt-oss-120b") wins over settings."""
    if override:
        name, _, model = override.partition("/")
        return provider_named(name.strip(), settings, model.strip() or None)
    name, model = resolve(task, settings)
    return provider_named(name, settings, model)


# ---------------------------------------------------------------------------
# Models that can read images
# ---------------------------------------------------------------------------

def ollama_vision_models(settings: dict[str, str]) -> list[str]:
    """Installed Ollama models that report the vision capability ([] when Ollama is not running)."""
    import requests
    url = (settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
    try:
        tags = requests.get(f"{url}/api/tags", timeout=(1, 3)).json().get("models") or []
    except (requests.RequestException, ValueError):
        return []
    found = []
    for m in tags:
        name = m.get("name") or m.get("model") or ""
        try:
            caps = requests.post(f"{url}/api/show", json={"model": name}, timeout=(1, 5)).json().get("capabilities")
        except (requests.RequestException, ValueError):
            continue
        if isinstance(caps, list) and "vision" in caps:
            found.append(name)
    return found


# Open-weight models on Groq that take images; offered when the account lists them (Groq retires models)
GROQ_VISION = ("qwen/qwen3.8-27b", "meta-llama/llama-4-scout-17b-16e-instruct",
               "meta-llama/llama-4-maverick-17b-128e-instruct")
_groq_models_cache: dict[str, tuple[float, list[str]]] = {}


def groq_models(settings: dict[str, str]) -> list[str]:
    """The models this Groq key can use (cached for 10 minutes; [] when Groq cannot be reached)."""
    import time
    import requests
    key = settings.get("groq_api_key") or ""
    if not key:
        return []
    hit = _groq_models_cache.get(key)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    try:
        resp = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"},
                            timeout=(3, 8))
        found = [m["id"] for m in resp.json().get("data", []) if m.get("active", True)] if resp.ok else []
    except (requests.RequestException, ValueError):
        found = []
    _groq_models_cache[key] = (time.monotonic(), found)
    return found


def _short(model: str) -> str:
    return model.removesuffix(":latest")


def vision_choices(settings: dict[str, str]) -> dict:
    """Providers that can read screenshots here: {"choices": [{spec, label, local, where}], "default": spec | None}.

    The default is llm_tasks.screenshots when set, otherwise the provider reports use when it
    can read images, otherwise the first choice.
    """
    models = _json_setting(settings, "llm_models")
    choices: list[dict] = []
    if settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"):
        from projectstack.llm.providers.claude import ClaudeProvider
        choices.append({"spec": "claude", "label": f"Claude ({models.get('claude') or ClaudeProvider.default_model})",
                        "local": False, "where": "Anthropic"})
    if settings.get("gemini_api_key"):
        choices.append({"spec": "gemini", "label": f"Gemini ({models.get('gemini') or 'Flash'})",
                        "local": False, "where": "Google"})
    if settings.get("openai_api_key"):
        from projectstack.llm.providers.openai_compat import OpenAIProvider
        choices.append({"spec": "openai", "label": f"OpenAI ({models.get('openai') or OpenAIProvider.default_model})",
                        "local": False, "where": "OpenAI"})
    available = groq_models(settings)
    choices += [{"spec": f"groq/{m}", "label": f"Groq {m.rsplit('/', 1)[-1]}", "local": False, "where": "Groq"}
                for m in GROQ_VISION if m in available]
    preferred = settings.get("ollama_model") or ""
    local = sorted(ollama_vision_models(settings), key=lambda m: _short(m) != _short(preferred))
    choices += [{"spec": f"ollama/{m}", "label": f"Ollama {_short(m)}", "local": True, "where": "this machine"}
                for m in local]

    spec = _json_setting(settings, "llm_tasks").get("screenshots")
    if spec:
        spec = str(spec)
        if not any(c["spec"] == spec for c in choices):
            name = spec.partition("/")[0]
            choices.insert(0, {"spec": spec, "label": spec, "local": name == "ollama",
                               "where": "this machine" if name == "ollama" else name})
        return {"choices": choices, "default": spec}
    name, _ = resolve("report", settings)
    default = next((c["spec"] for c in choices if c["spec"].partition("/")[0] == name), None)
    return {"choices": choices, "default": default or (choices[0]["spec"] if choices else None)}
