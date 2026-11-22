"""LLM provider interface.

The AI chat module depends only on this interface, never on a vendor SDK.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system", "user", "assistant" or "tool"
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = field(default=None, repr=False)


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int | None = None
    ) -> str:
        """Return a plain completion for ``messages``."""
        ...

    async def complete_with_tools(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return a completion that may request tool calls."""
        ...
