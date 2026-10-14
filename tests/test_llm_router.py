"""Gemini call: thinking-setting fallback, quota skipping, timeouts fail over (no network)."""

import pytest
import requests

from projectstack.correlator import llm_router


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload = status, payload or {}
        self.text = text or str(payload)
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload


def _ok(text="hello"):
    return _Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setattr(llm_router, "_gemini_models", lambda key: ["m-quota", "m-slow", "m-new"])
    llm_router._gemini_thinking_ok.clear()
    llm_router._gemini_cooldown.clear()
    monkeypatch.setattr(llm_router.time, "sleep", lambda s: None)


def test_skips_quota_and_timeouts_and_adapts_thinking(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        model = url.split("/models/")[1].split(":")[0]
        thinking = kw["json"]["generationConfig"].get("thinkingConfig")
        calls.append((model, thinking))
        if model == "m-quota":
            return _Resp(429, text="You exceeded your current quota")
        if model == "m-slow":
            raise requests.exceptions.ReadTimeout()
        if thinking == {"thinkingLevel": "low"}:
            return _Resp(400, text="Request contains an invalid argument.")
        return _ok("narrative")

    monkeypatch.setattr(requests, "post", fake_post)
    assert llm_router._gemini_generate("k", "p", max_output_tokens=1024) == "narrative"
    assert calls == [("m-quota", {"thinkingLevel": "low"}), ("m-slow", {"thinkingLevel": "low"}),
                     ("m-new", {"thinkingLevel": "low"}), ("m-new", {"thinkingBudget": 0})]
    # The quota-exhausted model is skipped on the next call
    calls.clear()
    llm_router._gemini_generate("k", "p")
    assert calls[0][0] != "m-quota"


def test_all_models_out_of_quota_gives_clear_message(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp(429, text="exceeded your current quota"))
    with pytest.raises(RuntimeError, match="used up its quota"):
        llm_router._gemini_generate("k", "p")
