"""Per-channel bot state that is not tied to a feature module.

Currently this holds the channel's "may the bot speak here?" flag. A missing
row means output is enabled, so existing channels keep working without a
backfill after migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models.base import Base


class ChannelState(Base):
    __tablename__ = "channel_states"

    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    output_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
