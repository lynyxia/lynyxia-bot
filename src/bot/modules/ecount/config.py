"""Configuration for the emote counter module."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class EcountConfig(BaseModel):
    # Fallback window when no timeframe (or an invalid one) is supplied.
    default_days: int = Field(default=365, ge=1, le=36_500)
    # Hard ceiling for requested timeframes, to keep regex scans bounded.
    max_days: int = Field(default=3_650, ge=1, le=36_500)

    @model_validator(mode="after")
    def _clamp_default(self) -> EcountConfig:
        if self.default_days > self.max_days:
            self.default_days = self.max_days
        return self
