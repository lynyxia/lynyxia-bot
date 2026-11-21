"""Persisted bot OAuth token pair.

One row per Twitch user; the bot normally stores exactly one row for its own
user ID. Tokens are secrets: access to this table should be restricted to the
application role.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models.base import Base


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
