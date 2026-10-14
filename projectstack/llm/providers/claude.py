"""
Anthropic Claude through the official SDK.

Defaults to Claude Opus 5.5 with adaptive thinking (the model's default when
`thinking` is omitted) and an explicit effort level per call. Structured output
uses output_config.format; tool definitions are strict when their schema allows.
When the installed SDK supports server-side refusal fallbacks, requests opt in
(`fallbacks="default"`), so a declined request is retried on a fallback model.
"""

from __future__ import annotations

import inspect
import re
from functools import lru_cache
from typing import Any

from projectstack.llm.base import (
    Completion, LLMConfigError, LLMError, LLMRefusal, Provider, ToolCall, ToolSpec, ToolTurn,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that take output_config.effort and structured outputs (older ones reject them)
_MODERN_RE = re.compile(r"claude-(opus-(4-[5-9]|5)|sonnet-(4-6|5)|fable|mythos|haiku-5)")


@lru_cache(maxsize=1)
def _sdk_supports_fallbacks() -> bool:
    try:
        from anthropic.resources.beta.messages import Messages
        return "fallbacks" in inspect.signature(Messages.create).parameters
    except Exception:
        return False


class ClaudeProvider(Provider):
    name = "claude"
    default_model = "claude-opus-5-5"

    def __init__(self, settings: dict[str, str], model: str | None = None):
        super().__init__(settings, model)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("The anthropic package is not installed — pip install anthropic") from exc
        self._anthropic = anthropic
        key = settings.get("anthropic_api_key") or None
        try:
            # Without a key in Settings the SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / a CLI profile
            self.client = anthropic.Anthropic(api_key=key, timeout=180.0, max_retries=2) if key else \
                anthropic.Anthropic(timeout=180.0, max_retries=2)
        except anthropic.AnthropicError as exc:
            raise LLMConfigError("No Anthropic API key — add anthropic_api_key in Settings or set ANTHROPIC_API_KEY") from exc

    @property
    def modern(self) -> bool:
        return bool(_MODERN_RE.search(self.model))

    # ------------------------------------------------------------------

    def _create(self, **kw: Any):
        a = self._anthropic
        try:
            if _sdk_supports_fallbacks():
                return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kw)
            return self.client.messages.create(**kw)
        except a.AuthenticationError as exc:
            raise LLMConfigError("Anthropic rejected the API key — check anthropic_api_key in Settings") from exc
        except a.PermissionDeniedError as exc:
            raise LLMConfigError(f"The Anthropic API key cannot use {self.model}: {exc.message}") from exc
        except a.NotFoundError as exc:
            raise LLMConfigError(f"Unknown Claude model {self.model!r}") from exc
        except a.RateLimitError as exc:
            raise LLMError("Claude is rate limiting this key — try again shortly") from exc
        except a.BadRequestError as exc:
            raise LLMError(f"Claude rejected the request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("Cannot reach the Anthropic API — check the network") from exc

    def _check(self, resp) -> None:
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusal(f"Claude declined the request{f' ({category})' if category else ''}")

    def _output_config(self, effort: str | None, json_schema: dict | None) -> dict | None:
        if not self.modern:
            return None
        cfg: dict[str, Any] = {"effort": effort or "medium"}
        if json_schema:
            cfg["format"] = {"type": "json_schema", "schema": json_schema}
        return cfg

    # ------------------------------------------------------------------

    def complete(self, system: str, prompt: str, *, max_tokens: int = 16000,
                 json_schema: dict | None = None, effort: str | None = None) -> Completion:
        kw: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens,
                              "messages": [{"role": "user", "content": prompt}]}
        if system:
            kw["system"] = system
        cfg = self._output_config(effort, json_schema)
        if cfg:
            kw["output_config"] = cfg
        elif json_schema:
            from projectstack.llm.base import schema_instruction
            kw["system"] = f"{system}\n\n{schema_instruction(json_schema)}".strip()
        resp = self._create(**kw)
        self._check(resp)
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"Claude's answer was cut off at {max_tokens} tokens")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(text, self.name, resp.model, resp.stop_reason,
                          resp.usage.input_tokens, resp.usage.output_tokens)

    def complete_with_tools(self, system: str, messages: list[Any], tools: list[ToolSpec], *,
                            max_tokens: int = 16000, effort: str | None = None) -> ToolTurn:
        defs = []
        for t in tools:
            d = {"name": t.name, "description": t.description, "input_schema": t.parameters}
            if t.parameters.get("additionalProperties") is False and "required" in t.parameters:
                d["strict"] = True
            defs.append(d)
        kw: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": messages, "tools": defs}
        if system:
            kw["system"] = system
        cfg = self._output_config(effort, None)
        if cfg:
            kw["output_config"] = cfg
        resp = self._create(**kw)
        self._check(resp)
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text")
        # The whole content goes back unchanged (thinking blocks included), keeping history append-only
        return ToolTurn(text, calls, [{"role": "assistant", "content": resp.content}], resp.stop_reason, resp.model,
                        {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens})

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[Any]:
        # Every result of a turn goes in one user message
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id, "content": output, "is_error": is_error}
            for call, output, is_error in results
        ]}]
