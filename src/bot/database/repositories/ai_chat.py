"""Repositories for AI chat settings, allowlists and thread context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.database.models import AiChatAllowlist, AiChatChannel, AiChatThread
from bot.database.repositories.base import BaseRepository


class AiChatChannelRepository(BaseRepository):
    async def get(self, channel_id: str) -> AiChatChannel | None:
        async with self.database.session() as session:
            return await session.get(AiChatChannel, channel_id)

    async def set_persona(self, channel_id: str, persona: str) -> None:
        async with self.database.session() as session:
            stmt = pg_insert(AiChatChannel).values(
                channel_id=channel_id, persona=persona
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["channel_id"], set_={"persona": persona}
                )
            )
            await session.commit()


class AiChatAllowlistRepository(BaseRepository):
    async def add(self, channel_id: str, user_id: str, login: str) -> None:
        async with self.database.session() as session:
            stmt = pg_insert(AiChatAllowlist).values(
                channel_id=channel_id, user_id=user_id, login=login
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["channel_id", "user_id"],
                    set_={"login": login},
                )
            )
            await session.commit()

    async def remove(self, channel_id: str, user_id: str) -> bool:
        async with self.database.session() as session:
            record = await session.get(AiChatAllowlist, (channel_id, user_id))
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True

    async def list(self, channel_id: str) -> Sequence[AiChatAllowlist]:
        async with self.database.session() as session:
            result = await session.execute(
                select(AiChatAllowlist)
                .where(AiChatAllowlist.channel_id == channel_id)
                .order_by(AiChatAllowlist.login)
            )
            return result.scalars().all()

    async def contains(self, channel_id: str, user_id: str) -> bool:
        async with self.database.session() as session:
            record = await session.get(AiChatAllowlist, (channel_id, user_id))
            return record is not None


class AiChatThreadRepository(BaseRepository):
    async def get(self, thread_id: str) -> AiChatThread | None:
        async with self.database.session() as session:
            return await session.get(AiChatThread, thread_id)

    async def save(
        self,
        thread_id: str,
        channel_id: str,
        user_id: str,
        persona: str,
        messages: list[dict[str, Any]],
    ) -> None:
        async with self.database.session() as session:
            stmt = pg_insert(AiChatThread).values(
                thread_id=thread_id,
                channel_id=channel_id,
                user_id=user_id,
                persona=persona,
                messages=messages,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["thread_id"],
                    set_={"persona": persona, "messages": messages},
                )
            )
            await session.commit()
