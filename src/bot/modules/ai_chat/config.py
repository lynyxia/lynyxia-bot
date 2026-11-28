"""Configuration for the AI chat module."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AIChatConfig(BaseModel):
    personas_dir: str = "personas"
    default_persona: str = "generic.md"
    cooldown_seconds: float = Field(default=5.0, ge=0, le=300)
    max_context_messages: int = Field(default=20, ge=1, le=200)
    max_thread_messages: int = Field(default=20, ge=1, le=200)
    max_response_tokens: int = Field(default=500, ge=1, le=8000)
    # Three tool calls plus the final answer need four rounds.
    max_tool_rounds: int = Field(default=5, ge=1, le=10)
    tools: list[str] = Field(
        default_factory=lambda: [
            "web_search",
            "chat_messages",
            "list_chatters",
            "sample_chatter_messages",
            "search_chat",
        ]
    )
    searxng_url: str | None = None
    searxng_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    searxng_max_results: int = Field(default=5, ge=1, le=50)
    log_path: str = "logs/ai.log"

    @field_validator("tools", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("tools", "default_persona", "personas_dir", "log_path")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
