"""
Shared LLM layer: one provider interface (complete / complete_with_tools) for
Claude, OpenAI, Groq, Gemini and Ollama, per-task provider selection, and
versioned prompt templates. Used by correlation, reports and the ask agent.
"""

from projectstack.llm.base import (  # noqa: F401
    Completion, LLMConfigError, LLMError, LLMRefusal, Provider, ToolCall, ToolSpec, ToolTurn,
)
from projectstack.llm.prompts import Prompt, load_prompt  # noqa: F401
from projectstack.llm.registry import provider_for_task, provider_named, resolve  # noqa: F401
