"""Per-channel AI chat settings, allowlists and thread context."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models.base import Base


class AiChatChannel(Base):
    __tablename__ = "ai_chat_channels"

    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    persona: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AiChatAllowlist(Base):
    __tablename__ = "ai_chat_allowlist"
    __table_args__ = (Index("ix_ai_chat_allowlist_channel", "channel_id"),)

    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    login: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AiChatThread(Base):
    __tablename__ = "ai_chat_threads"
    __table_args__ = (Index("ix_ai_chat_threads_channel", "channel_id"),)

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    persona: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    messages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
