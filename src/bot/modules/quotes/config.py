"""Configuration for the quotes module."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class QuotesConfig(BaseModel):
    # Twitch logins or numeric user IDs. Matching is case-insensitive.
    excluded_users: list[str] = Field(default_factory=list)

    @field_validator("excluded_users", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("excluded_users")
    @classmethod
    def _normalize(cls, value: list[str]) -> list[str]:
        normalized = {entry.strip().removeprefix("@").lower() for entry in value}
        normalized.discard("")
        return sorted(normalized)
