"""Google Gemini (REST, with model discovery and quota fail-over) and Ollama (local models such as Mistral)."""

from __future__ import annotations

import json
import time
from typing import Any

from projectstack.llm.base import (
    Completion, LLMConfigError, LLMError, Provider, ToolCall, ToolSpec, ToolTurn, schema_instruction,
)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini_schema(schema: Any) -> Any:
    """JSON Schema → the OpenAPI subset Gemini's functionDeclarations accept (no additionalProperties)."""
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items() if k not in ("additionalProperties", "$schema")}
    if isinstance(schema, list):
        return [_gemini_schema(v) for v in schema]
    return schema


class GeminiProvider(Provider):
    name = "gemini"
    default_model = ""          # empty: discover the best available Flash model for the key

    def __init__(self, settings: dict[str, str], model: str | None = None):
        super().__init__(settings, model)
        self.key = settings.get("gemini_api_key", "")
        if not self.key:
            raise LLMConfigError("No Gemini API key — add gemini_api_key in Settings (free at aistudio.google.com)")

    @property
    def label(self) -> str:
        return f"gemini/{self.model or 'flash (auto)'}"

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192,
                 json_schema: dict | None = None, effort: str | None = None) -> Completion:
        # The transport (model discovery, thinking settings, quota cooldowns) lives in llm_router
        from projectstack.correlator import llm_router
        if json_schema:
            system = f"{system}\n\n{schema_instruction(json_schema)}".strip()
        try:
            text = llm_router._gemini_generate(self.key, prompt, system or None, max_output_tokens=max_tokens,
                                               json_mode=bool(json_schema), pin_model=self.model or None)
        except ValueError as exc:
            raise LLMConfigError(str(exc)) from exc
        except RuntimeError as exc:
            raise LLMError(str(exc)) from exc
        usage = getattr(llm_router._gemini_last, "usage", {}) or {}
        return Completion(text, self.name, getattr(llm_router._gemini_last, "model", "") or self.model or "gemini-flash",
                          "", usage.get("promptTokenCount"),
                          (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0) or None)

    # -- tool use ------------------------------------------------------------
    # One model answers the whole conversation: Gemini's thought signatures are tied to it.

    def user_message(self, text: str) -> Any:
        return {"role": "user", "parts": [{"text": text}]}

    def _post(self, model: str, body: dict) -> Any:
        import requests
        return requests.post(_GEMINI_URL.format(model=model), json=body, timeout=(10, 90),
                             headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})

    def complete_with_tools(self, system: str, messages: list[Any], tools: list[ToolSpec], *,
                            max_tokens: int = 8192, effort: str | None = None) -> ToolTurn:
        import requests
        from projectstack.correlator import llm_router
        body: dict[str, Any] = {
            "contents": messages,
            "tools": [{"functionDeclarations": [{"name": t.name, "description": t.description,
                                                 "parameters": _gemini_schema(t.parameters)} for t in tools]}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        pinned = getattr(self, "_tool_model", None) or self.model
        if pinned:
            candidates = [pinned]
        else:
            now = time.monotonic()
            found = llm_router._gemini_models(self.key)
            candidates = [m for m in found if llm_router._gemini_cooldown.get((self.key, m), 0) <= now] or found
        errors = []
        for model in candidates:
            # Keep thinking low (as for reports): the Flash models otherwise think for minutes
            options = ([llm_router._gemini_thinking_ok[model]] if model in llm_router._gemini_thinking_ok
                       else list(llm_router._THINKING_OPTIONS))
            resp = None
            for option in options:
                body["generationConfig"] = {"maxOutputTokens": max_tokens, **({"thinkingConfig": option} if option else {})}
                try:
                    resp = self._post(model, body)
                except (requests.Timeout, requests.ConnectionError) as exc:
                    if pinned:
                        raise LLMError(f"Gemini ({model}) did not answer ({type(exc).__name__})") from exc
                    errors.append(f"{model}: {type(exc).__name__}")
                    resp = None
                    break
                if resp.status_code == 400 and option is not None and "API key" not in resp.text:
                    continue                    # this thinking setting is not supported by the model
                break
            if resp is None:
                continue
            if resp.ok:
                llm_router._gemini_thinking_ok.setdefault(model, option)
                self._tool_model = model
                return self._turn(model, resp.json())
            if resp.status_code in (400, 401, 403) and "API key" in resp.text:
                raise LLMConfigError("Gemini rejected the API key — check gemini_api_key in Settings")
            if resp.status_code in (404, 429, 503) and not pinned:
                if resp.status_code == 429:
                    llm_router._gemini_cooldown[(self.key, model)] = time.monotonic() + 900
                errors.append(f"{model}: HTTP {resp.status_code}")
                continue
            try:
                msg = resp.json().get("error", {}).get("message") or resp.text
            except ValueError:
                msg = resp.text
            raise LLMError(f"Gemini error {resp.status_code} from {model}: {msg[:300]}")
        raise LLMError(f"No Gemini model answered ({'; '.join(errors) or 'none available'})")

    def _turn(self, model: str, data: dict) -> ToolTurn:
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason})")
        content = cands[0].get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        parts = content.get("parts") or []
        calls = [ToolCall(p["functionCall"].get("id") or f"auto-{i}", p["functionCall"]["name"],
                          dict(p["functionCall"].get("args") or {}))
                 for i, p in enumerate(parts) if "functionCall" in p]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        return ToolTurn(text, calls, [content], cands[0].get("finishReason", ""), model,
                        {"input_tokens": usage.get("promptTokenCount") or 0,
                         "output_tokens": (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)})

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[Any]:
        parts = []
        for call, output, is_error in results:
            response = {"name": call.name, "response": {"error" if is_error else "result": output}}
            if not call.id.startswith("auto-"):
                response["id"] = call.id
            parts.append({"functionResponse": response})
        return [{"role": "user", "parts": parts}]


class OllamaProvider(Provider):
    name = "ollama"
    default_model = ""          # empty: the ollama_model setting

    def __init__(self, settings: dict[str, str], model: str | None = None):
        super().__init__(settings, model or settings.get("ollama_model") or "mistral")
        self.url = (settings.get("ollama_url") or "http://localhost:11434").rstrip("/")

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192,
                 json_schema: dict | None = None, effort: str | None = None) -> Completion:
        import requests
        if json_schema:
            system = f"{system}\n\n{schema_instruction(json_schema)}".strip()
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body: dict = {"model": self.model, "messages": messages, "stream": False,
                      "options": {"temperature": 0, "num_predict": max_tokens}}
        if json_schema:
            body["format"] = "json"
        try:
            resp = requests.post(f"{self.url}/api/chat", json=body, timeout=(5, 300))
        except requests.ConnectionError as exc:
            raise LLMConfigError(f"Ollama is not running at {self.url} — start it or pick another provider") from exc
        if resp.status_code == 404:
            raise LLMConfigError(f"Ollama has no model {self.model!r} — run: ollama pull {self.model}")
        if not resp.ok:
            raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return Completion(data.get("message", {}).get("content", ""), self.name, self.model,
                          data.get("done_reason", ""), data.get("prompt_eval_count"), data.get("eval_count"))

    def complete_with_tools(self, system: str, messages: list[Any], tools: list[ToolSpec], *,
                            max_tokens: int = 8192, effort: str | None = None) -> ToolTurn:
        import requests
        body = {"model": self.model, "stream": False, "options": {"temperature": 0, "num_predict": max_tokens},
                "messages": ([{"role": "system", "content": system}] if system else []) + list(messages),
                "tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                            "parameters": t.parameters}} for t in tools]}
        try:
            resp = requests.post(f"{self.url}/api/chat", json=body, timeout=(5, 300))
        except requests.ConnectionError as exc:
            raise LLMConfigError(f"Ollama is not running at {self.url} — start it or pick another provider") from exc
        if resp.status_code == 404:
            raise LLMConfigError(f"Ollama has no model {self.model!r} — run: ollama pull {self.model}")
        if resp.status_code == 400 and "does not support tools" in resp.text:
            raise LLMConfigError(f"The Ollama model {self.model!r} does not support tool calling")
        if not resp.ok:
            raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        msg = data.get("message") or {"role": "assistant", "content": ""}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_invalid_json": args}
            calls.append(ToolCall(tc.get("id") or f"call-{i}", fn.get("name", ""), args))
        return ToolTurn(msg.get("content") or "", calls, [msg], data.get("done_reason", ""), self.model,
                        {"input_tokens": data.get("prompt_eval_count") or 0, "output_tokens": data.get("eval_count") or 0})

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[Any]:
        return [{"role": "tool", "tool_name": call.name, "content": (f"ERROR: {out}" if err else out)}
                for call, out, err in results]
