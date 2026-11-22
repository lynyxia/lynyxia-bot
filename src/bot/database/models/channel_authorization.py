"""Broadcaster authorization for app-token chat sends.

Twitch requires each channel to grant ``channel:bot`` (or the bot to be a
moderator) before an app access token can send messages in that channel with
the Chat Bot badge.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models.base import Base


class ChannelAuthorization(Base):
    __tablename__ = "channel_authorizations"

    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    broadcaster_login: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
