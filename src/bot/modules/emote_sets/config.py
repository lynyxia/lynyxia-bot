"""Configuration for the emote-set comparison module."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

SAFE_FILENAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class EmoteSetsConfig(BaseModel):
    filename: str = "emotesets.html"
    # Comparing every pair is O(n^2); cap the number of sets in the report.
    max_sets: int = Field(default=10, ge=2, le=50)

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        name = value.strip()
        if SAFE_FILENAME.fullmatch(name) is None or ".." in name:
            raise ValueError("Invalid report filename")
        return name
