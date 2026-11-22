"""Shared per-channel output gate.

Every outbound chat path consults this service before sending. State lives in
PostgreSQL so a disable survives restarts, and the first read is cached to
avoid a database round-trip on every message. Channels with no stored row are
treated as enabled, which keeps existing deployments talking by default.
"""

from __future__ import annotations

import logging

from bot.database.repositories.channel_state import ChannelStateRepository

log = logging.getLogger(__name__)


class ChannelOutputGate:
    def __init__(self, repository: ChannelStateRepository) -> None:
        self._repository = repository
        self._cache: dict[str, bool] = {}

    async def is_enabled(self, channel_id: str) -> bool:
        if channel_id not in self._cache:
            try:
                self._cache[channel_id] = await self._repository.is_enabled(channel_id)
            except Exception:
                # Fail open: a database problem must never silence the bot.
                log.exception(
                    "Could not read output state for channel %s; allowing output",
                    channel_id,
                )
                return True
        return self._cache[channel_id]

    async def set_enabled(self, channel_id: str, enabled: bool) -> None:
        await self._repository.set_output_enabled(channel_id, enabled)
        self._cache[channel_id] = enabled
