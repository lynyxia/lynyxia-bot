"""LLM provider interface.

Only the interface is defined in this milestone. A concrete OpenAI-compatible
adapter (and its dependency) will be added together with the AI Chat module,
so this foundation stage introduces no extra frameworks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system", "user" or "assistant"
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int | None = None
    ) -> str:
        """Return a completion for ``messages``."""
        ...
