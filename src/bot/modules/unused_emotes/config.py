"""Configuration for the unused-emotes report module.

Shared report settings (URL prefix, directory, 7TV base URL) live in
``BOT_REPORTS``; this file only holds module-specific behavior.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

CUTOFF_PATTERN = re.compile(r"\d+[ywdm]", re.IGNORECASE)
SAFE_FILENAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class UnusedEmotesConfig(BaseModel):
    default_cutoff: str = "365d"
    default_threshold: int = 10
    max_threshold: int = 10_000
    filename: str = Field(default="unusedemotes.html")

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
