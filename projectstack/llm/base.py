"""
The provider interface every LLM integration implements, shared by correlation,
reports and the `ask` agent.

  complete(system, prompt)                     one prompt, one answer
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
                 json_schema: dict | None = None, effort: str | None = None) -> Completion:
        """Answer one prompt. With json_schema, the answer is JSON (enforced where the provider can)."""

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
