"""Centralized application configuration via Pydantic Settings.

Every environment variable is read here — never scattered throughout the code.
Nested settings use a ``__`` delimiter, e.g. ``BOT_TWITCH__CLIENT_ID``.
Secrets are typed as ``SecretStr`` so they are redacted in reprs and logs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TwitchSettings(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    bot_username: str = Field(min_length=1)
    # TwitchIO 3 uses EventSub, which needs stable numeric IDs, not logins.
    bot_id: str = Field(min_length=1)
    # A single bot can listen in multiple channels. Names are informational;
    # IDs are what EventSub subscriptions use. A single string is accepted for
    # backwards compatibility and normalized to a one-element list.
    channel: list[str] = Field(min_length=1)
    channel_id: list[str] = Field(min_length=1)
    # OAuth is managed by the bot: it persists tokens in PostgreSQL and
    # refreshes them automatically. Only the app credentials are configured.
    oauth_redirect_uri: str = "http://localhost:8080/oauth/callback"
    oauth_bind_host: str = "0.0.0.0"
    oauth_port: int = 8080
    oauth_scopes: list[str] = Field(
        default_factory=lambda: [
            "user:read:chat",
            "user:write:chat",
            "user:bot",
            "channel:bot",
        ]
    )
    # Scopes a broadcaster grants so the app token can send in their channel.
    channel_scopes: list[str] = Field(default_factory=lambda: ["channel:bot"])

    @field_validator("bot_id")
    @classmethod
    def _validate_bot_id(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("Twitch IDs must be numeric strings")
        return value

    @field_validator(
        "channel", "channel_id", "oauth_scopes", "channel_scopes", mode="before"
    )
    @classmethod
    def _as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("channel_id")
    @classmethod
    def _validate_channel_ids(cls, value: list[str]) -> list[str]:
        for channel_id in value:
            if not channel_id.isdigit():
                raise ValueError("Twitch channel IDs must be numeric strings")
        return value

    @model_validator(mode="after")
    def _check_channels(self) -> TwitchSettings:
        if not self.channel_id:
            raise ValueError("At least one channel ID is required")
        if len(set(self.channel_id)) != len(self.channel_id):
            raise ValueError("Channel IDs must be unique")
        if len(self.channel) != len(self.channel_id):
            raise ValueError("BOT_TWITCH__CHANNEL and BOT_TWITCH__CHANNEL_ID must match in length")
        return self

    @field_validator("oauth_redirect_uri")
    @classmethod
    def _validate_redirect_uri(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("OAuth redirect URI must be an http(s) URL")
        return value.rstrip("/")


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


class ChatSettings(BaseModel):
    min_interval_seconds: float = Field(default=1.5, ge=0, le=60)
    queue_maxsize: int = Field(default=100, ge=1)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_base_seconds: float = Field(default=1.0, ge=0, le=30)
    # Case-insensitive substrings; matched messages are replaced before sending.
    banned_phrases: list[str] = Field(default_factory=list)
    banned_phrase_replacement: str = (
        "[System] Blocklisted phrase detected in reply... Aborting."
    )

    @field_validator("banned_phrases", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("banned_phrases")
    @classmethod
    def _normalize_phrases(cls, value: list[str]) -> list[str]:
        phrases = {entry.strip().lower() for entry in value}
        phrases.discard("")
        return sorted(phrases)


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
    chat: ChatSettings = Field(default_factory=ChatSettings)
    enabled_modules: list[str] = Field(
        default_factory=lambda: ["chat_logger", "quotes", "ecount", "lines", "ai_chat"]
    )
    admin_user_ids: list[str] = Field(default_factory=list)
    # Module-specific configuration, keyed by module name.
    # e.g. BOT_MODULE__AI_CHAT='{"system_prompt": "..."}'
    module: dict[str, dict[str, Any]] = Field(default_factory=dict)
