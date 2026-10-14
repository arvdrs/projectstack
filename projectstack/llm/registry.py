"""
Which provider and model each task uses.

Settings (JSON strings in ~/.projectstack_settings.json):

  llm_tasks   {"correlation": "ollama/mistral", "report": "claude", "ask": "claude/claude-opus-5-5"}
  llm_models  {"claude": "claude-opus-5-5", "groq": "llama-3.3-70b-versatile"}   per-provider default model

Without llm_tasks: correlation uses the provider chosen in the web app (ai_provider);
report and ask use Claude when an Anthropic key is available, otherwise Gemini when
a Gemini key is set, otherwise ai_provider.
"""

from __future__ import annotations

import json
import os

from projectstack.llm.base import LLMConfigError, Provider

TASKS = ("correlation", "report", "ask")


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
    """The provider for a task; `override` ("claude" or "groq/llama-3.3-70b-versatile") wins over settings."""
    if override:
        name, _, model = override.partition("/")
        return provider_named(name.strip(), settings, model.strip() or None)
    name, model = resolve(task, settings)
    return provider_named(name, settings, model)
