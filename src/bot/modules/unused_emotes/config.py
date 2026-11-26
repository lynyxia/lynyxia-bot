"""Configuration for the unused-emotes report module."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

CUTOFF_PATTERN = re.compile(r"\d+[ywdm]", re.IGNORECASE)
SAFE_FILENAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class UnusedEmotesConfig(BaseModel):
    # Public prefix used to turn the report path into a clickable chat link.
    # Empty means reporting is available but no link can be posted.
    url_prefix: str = ""
    api_base: str = "https://7tv.io/v3"
    default_cutoff: str = "365d"
    default_threshold: int = 10
    max_threshold: int = 10_000
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    # Relative to /app in Docker; the compose mount makes it visible on the host.
    reports_dir: str = "reports"
    filename: str = "unusedemotes.html"

    @field_validator("url_prefix", "api_base", "reports_dir", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url_prefix", "api_base")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value:
            return value
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("default_cutoff")
    @classmethod
    def _validate_cutoff(cls, value: str) -> str:
        if CUTOFF_PATTERN.fullmatch(value.strip()) is None:
            raise ValueError("default_cutoff must look like 365d, 6m, 2w or 1y")
        return value.strip().lower()

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        name = value.strip()
        if SAFE_FILENAME.fullmatch(name) is None or ".." in name:
            raise ValueError("Invalid report filename")
        return name

    @model_validator(mode="after")
    def _check_thresholds(self) -> UnusedEmotesConfig:
        if self.default_threshold < 1 or self.max_threshold < self.default_threshold:
            raise ValueError("Invalid threshold settings")
        return self
