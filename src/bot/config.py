"""Centralized application configuration via Pydantic Settings.

Every environment variable is read here — never scattered throughout the code.
Nested settings use a ``__`` delimiter, e.g. ``BOT_TWITCH__CLIENT_ID``.
Secrets are typed as ``SecretStr`` so they are redacted in reprs and logs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TwitchSettings(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    bot_username: str = Field(min_length=1)
    oauth_token: SecretStr
    channel: str = Field(min_length=1)


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    user: str = "bot"
    password: SecretStr
    name: str = "twitch_bot"
    pool_size: int = 5
    max_overflow: int = 10
    # Optional full DSN override. Useful for tests and alternative deployments.
    url: str | None = None
    connect_retries: int = 10
    connect_retry_delay: float = 1.0

    @property
    def dsn(self) -> str:
        if self.url:
            return self.url
        password = self.password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.user}:{password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class LLMSettings(BaseModel):
    provider: str = "openai"
    api_key: SecretStr | None = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    timeout_seconds: float = 30.0
    max_response_chars: int = 500


class LoggingSettings(BaseModel):
    level: str = "INFO"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    twitch: TwitchSettings
    database: DatabaseSettings
    llm: LLMSettings = Field(default_factory=LLMSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    enabled_modules: list[str] = Field(
        default_factory=lambda: ["chat_logger", "quotes", "ai_chat"]
    )
    admin_user_ids: list[str] = Field(default_factory=list)
    # Module-specific configuration, keyed by module name.
    # e.g. BOT_MODULE__AI_CHAT='{"system_prompt": "..."}'
    module: dict[str, dict[str, Any]] = Field(default_factory=dict)
