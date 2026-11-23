"""OpenAI and Groq (OpenAI-compatible API) through the openai package."""

from __future__ import annotations

import json
from typing import Any

from projectstack.llm.base import (
    Completion, Image, LLMConfigError, LLMError, LLMRefusal, Provider, ToolCall, ToolSpec, ToolTurn, image_label,
    schema_instruction,
)


class _OpenAICompat(Provider):
    key_field = ""
    base_url: str | None = None
    product = ""
    max_retries = 1

    def __init__(self, settings: dict[str, str], model: str | None = None):
        super().__init__(settings, model)
        key = settings.get(self.key_field, "")
        if not key:
            raise LLMConfigError(f"No {self.product} API key — add {self.key_field} in Settings")
        from openai import OpenAI
        # The SDK waits as long as a 429's Retry-After asks before trying again
        self.client = OpenAI(api_key=key, base_url=self.base_url, timeout=120, max_retries=self.max_retries)

    def _create(self, **kw: Any):
        import openai
        try:
            return self.client.chat.completions.create(model=self.model, temperature=0.0, **kw)
        except openai.AuthenticationError as exc:
            raise LLMConfigError(f"{self.product} rejected the API key — check {self.key_field} in Settings") from exc
        except openai.RateLimitError as exc:
            raise LLMError(f"{self.product} rate limit or quota reached — try again later") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"{self.product} API error {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"Cannot reach {self.product}") from exc

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192,
                 json_schema: dict | None = None, effort: str | None = None,
                 images: list[Image] | None = None) -> Completion:
        if json_schema:
            system = f"{system}\n\n{schema_instruction(json_schema)}".strip()
        content: Any = prompt
        if images:
            content = [part for i, im in enumerate(images, 1) for part in (
                {"type": "text", "text": image_label(i, im)},
                {"type": "image_url", "image_url": {"url": f"data:{im.media_type};base64,{im.b64}"}},
            )] + [{"type": "text", "text": prompt}]
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        kw: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens}
        if json_schema:
            kw["response_format"] = {"type": "json_object"}
        resp = self._create(**kw)
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMRefusal(f"{self.product} declined the request: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise LLMError(f"{self.product}'s answer was cut off at {max_tokens} tokens")
        usage = resp.usage
        return Completion(choice.message.content or "", self.name, resp.model, choice.finish_reason or "",
                          getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))

    def complete_with_tools(self, system: str, messages: list[Any], tools: list[ToolSpec], *,
                            max_tokens: int = 8192, effort: str | None = None) -> ToolTurn:
        full = ([{"role": "system", "content": system}] if system else []) + list(messages)
        defs = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}} for t in tools]
        resp = self._create(messages=full, tools=defs, tool_choice="auto", max_tokens=max_tokens)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        native = msg.model_dump(exclude_none=True)
        native.setdefault("content", msg.content or "")
        return ToolTurn(msg.content or "", calls, [native], resp.choices[0].finish_reason or "", resp.model)

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[Any]:
        return [{"role": "tool", "tool_call_id": call.id, "content": (f"ERROR: {out}" if err else out)}
                for call, out, err in results]


class OpenAIProvider(_OpenAICompat):
    name = "openai"
    default_model = "gpt-4o-mini"
    key_field = "openai_api_key"
    product = "OpenAI"


class GroqProvider(_OpenAICompat):
    name = "groq"
    default_model = "openai/gpt-oss-120b"    # Llama 3.3 70B was retired by Groq
    key_field = "groq_api_key"
    base_url = "https://api.groq.com/openai/v1"
    product = "Groq"
    max_retries = 4               # the free tier's tokens-per-minute limit is easy to reach with pictures
