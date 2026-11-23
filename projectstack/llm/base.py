"""
The provider interface every LLM integration implements, shared by correlation,
reports and the `ask` agent.

  complete(system, prompt)                     one prompt, one answer
  complete(system, prompt, images=[...])       the same, with pictures for a vision model to read
  complete_with_tools(system, messages, tools) one turn of a tool-using conversation

Conversation history is kept in each provider's own message format (built with
user_message / tool_results and the messages a ToolTurn returns), so nothing a
provider needs to see again — such as Claude's thinking blocks — is lost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """The provider failed; the message is meant for the user."""


class LLMConfigError(LLMError):
    """The provider is not set up (no key, unknown provider, unreachable local server)."""


class LLMRefusal(LLMError):
    """The model declined the request."""


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    stop_reason: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Image:
    """A picture for a vision-capable model: the file's bytes and media type."""
    data: bytes
    media_type: str                     # image/png, image/jpeg, image/webp or image/gif
    name: str = ""

    @property
    def b64(self) -> str:
        import base64
        return base64.b64encode(self.data).decode("ascii")


def image_label(i: int, image: Image) -> str:
    """The text placed before the i-th picture (1-based), so the prompt can refer to "Image 2"."""
    return f"Image {i}" + (f" ({image.name})" if image.name else "")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema of the arguments


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolTurn:
    text: str
    tool_calls: list[ToolCall]
    messages: list[Any]                 # provider-native messages to append to the history
    stop_reason: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class Provider(ABC):
    name = "provider"
    default_model = ""

    def __init__(self, settings: dict[str, str], model: str | None = None):
        self.settings = settings
        self.model = model or self.default_model

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}" if self.model else self.name

    @abstractmethod
    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192,
                 json_schema: dict | None = None, effort: str | None = None,
                 images: list[Image] | None = None) -> Completion:
        """Answer one prompt. With json_schema, the answer is JSON (enforced where the provider can).
        With images, the pictures go before the prompt, each after an "Image N" label; the model
        must be able to read images (LLMConfigError where the provider can tell it cannot)."""

    def warm_up(self) -> None:
        """Before a long job of several calls: find out which models answer (no-op unless a provider fails over)."""

    def complete_with_tools(self, system: str, messages: list[Any], tools: list[ToolSpec], *,
                            max_tokens: int = 8192, effort: str | None = None) -> ToolTurn:
        raise LLMError(f"Tool use is not available for {self.name} yet")

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[Any]:
        """Messages carrying tool outputs: (call, output text, is_error) for every call of the last turn."""
        raise LLMError(f"Tool use is not available for {self.name} yet")


def schema_instruction(schema: dict) -> str:
    """For providers without schema enforcement: describe the JSON expected."""
    import json
    return ("Respond with a single JSON object (no prose, no code fences) that matches this JSON Schema:\n"
            + json.dumps(schema, indent=1))
