"""Persistence for per-channel broadcaster authorizations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from bot.database.models import ChannelAuthorization
from bot.database.repositories.base import BaseRepository


class ChannelAuthorizationRepository(BaseRepository):
    async def get(self, channel_id: str) -> ChannelAuthorization | None:
        async with self.database.session() as session:
            return await session.get(ChannelAuthorization, channel_id)

    async def list_all(self) -> Sequence[ChannelAuthorization]:
        async with self.database.session() as session:
            result = await session.execute(select(ChannelAuthorization))
            return result.scalars().all()

    async def save(
        self,
        channel_id: str,
        broadcaster_login: str,
        access_token: str,
        refresh_token: str,
        scopes: str = "",
    ) -> None:
        async with self.database.session() as session:
            record = await session.get(ChannelAuthorization, channel_id)
            if record is None:
                record = ChannelAuthorization(channel_id=channel_id)
                session.add(record)
            record.broadcaster_login = broadcaster_login
            record.access_token = access_token
            record.refresh_token = refresh_token
            record.scopes = scopes
            await session.commit()

    async def delete(self, channel_id: str) -> None:
        async with self.database.session() as session:
            record = await session.get(ChannelAuthorization, channel_id)
            if record is not None:
                await session.delete(record)
                await session.commit()
