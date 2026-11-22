"""Persistence for per-channel bot state."""

from __future__ import annotations

from bot.database.models import ChannelState
from bot.database.repositories.base import BaseRepository


class ChannelStateRepository(BaseRepository):
    async def get(self, channel_id: str) -> ChannelState | None:
        async with self.database.session() as session:
            return await session.get(ChannelState, channel_id)

    async def is_enabled(self, channel_id: str) -> bool:
        """Return whether chat output is enabled (default: yes)."""
        record = await self.get(channel_id)
        return True if record is None else bool(record.output_enabled)

    async def set_output_enabled(self, channel_id: str, enabled: bool) -> None:
        async with self.database.session() as session:
            record = await session.get(ChannelState, channel_id)
            if record is None:
                record = ChannelState(channel_id=channel_id)
                session.add(record)
            record.output_enabled = enabled
            await session.commit()
