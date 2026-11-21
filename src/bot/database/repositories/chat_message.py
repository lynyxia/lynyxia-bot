"""Persistence and quote queries for chat messages."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select

from bot.database.models import ChatMessage, User
from bot.database.repositories.base import BaseRepository


class ChatMessageRepository(BaseRepository):
    async def add(
        self,
        *,
        channel_id: str,
        user_id: str,
        content: str,
        sent_at: datetime | None = None,
        twitch_message_id: str | None = None,
    ) -> ChatMessage:
        fields: dict[str, object] = {
            "channel_id": channel_id,
            "user_id": user_id,
            "content": content,
            "twitch_message_id": twitch_message_id,
        }
        if sent_at is not None:
            fields["sent_at"] = sent_at
        message = ChatMessage(**fields)  # type: ignore[arg-type]
        async with self.database.session() as session:
            session.add(message)
            await session.commit()
            await session.refresh(message)
        return message

    async def add_many(self, messages: Sequence[ChatMessage]) -> None:
        async with self.database.session() as session:
            session.add_all(messages)
            await session.commit()

    async def get_random(self, channel_id: str) -> ChatMessage | None:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.channel_id == channel_id)
            .order_by(func.random())
            .limit(1)
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_random_by_user(self, channel_id: str, user_login: str) -> ChatMessage | None:
        stmt = (
            select(ChatMessage)
            .join(User, ChatMessage.user_id == User.id)
            .where(
                ChatMessage.channel_id == channel_id,
                func.lower(User.login) == user_login.lower(),
            )
            .order_by(func.random())
            .limit(1)
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
