"""Configuration for the chat logger module."""

from pydantic import BaseModel, Field


class ChatLoggerConfig(BaseModel):
    batch_size: int = Field(default=50, ge=1, le=1000)
    flush_interval_seconds: float = Field(default=2.0, gt=0, le=60.0)
    queue_maxsize: int = Field(default=1000, ge=1)
    # Extension point: retention cleanup is not scheduled yet, but the
    # repository already exposes delete_before() for a future maintenance job.
    retention_days: int | None = Field(default=None, ge=1)
