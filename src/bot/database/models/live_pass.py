"""Per-channel list of users allowed to use the bot while the streamer is live.

A user must be on this list to run bot commands during a live stream. The
channel owner and bot admins are always allowed implicitly (enforced by the
gate, not stored here).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models.base import Base


class LivePass(Base):
    __tablename__ = "live_passes"

    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    login: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    added_by: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
