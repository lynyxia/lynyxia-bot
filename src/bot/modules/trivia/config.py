"""Configuration for the trivia module."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class TriviaConfig(BaseModel):
    # Relative paths resolve from the working directory (/app in Docker).
    directory: str = "trivia"
    file_prefix: str = "category_"
    file_suffix: str = ".json"
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600)
    hint_delay_seconds: float = Field(default=10.0, ge=0, le=3600)
    similarity_threshold: float = Field(default=0.9, ge=0.5, le=1.0)

    @field_validator("directory", "file_prefix", "file_suffix", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _clamp_hint_delay(self) -> TriviaConfig:
        if self.hint_delay_seconds > self.timeout_seconds:
            self.hint_delay_seconds = self.timeout_seconds
        return self
