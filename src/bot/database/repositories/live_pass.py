"""Persistence for the per-channel live pass lists."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.database.models import LivePass
from bot.database.repositories.base import BaseRepository


class LivePassRepository(BaseRepository):
    async def add(
        self, channel_id: str, user_id: str, login: str, added_by: str = ""
    ) -> None:
        async with self.database.session() as session:
            stmt = pg_insert(LivePass).values(
                channel_id=channel_id,
                user_id=user_id,
                login=login,
                added_by=added_by,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["channel_id", "user_id"],
                    set_={"login": login, "added_by": added_by},
                )
            )
            await session.commit()

    async def remove(self, channel_id: str, user_id: str) -> bool:
        async with self.database.session() as session:
            record = await session.get(LivePass, (channel_id, user_id))
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True

    async def list(self, channel_id: str) -> Sequence[LivePass]:
        async with self.database.session() as session:
            result = await session.execute(
                select(LivePass)
                .where(LivePass.channel_id == channel_id)
                .order_by(LivePass.login)
            )
            return result.scalars().all()

    async def contains(self, channel_id: str, user_id: str) -> bool:
        async with self.database.session() as session:
            record = await session.get(LivePass, (channel_id, user_id))
            return record is not None
