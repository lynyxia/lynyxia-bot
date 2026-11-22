"""Configuration for the message counter module."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LinesConfig(BaseModel):
    # strftime format for the "since <date>" part of the reply (UTC).
    date_format: str = Field(default="%Y-%m-%d %H:%M:%S", min_length=1)
