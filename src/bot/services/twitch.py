"""Thin Twitch service surfaced to modules.

The concrete client is bound by the Application at startup. Modules call
this service rather than reaching into the TwitchIO bot directly.

TwitchIO 3.x sends chat through a channel's ``PartialUser`` rather than a
client-level ``send_message``, so this service wraps that detail.
"""

from __future__ import annotations

from typing import Any


class TwitchService:
    def __init__(self) -> None:
        self._client: Any = None

    def bind(self, client: Any) -> None:
        self._client = client

    async def send_message(self, channel_id: str, content: str) -> None:
        """Send ``content`` to the channel identified by ``channel_id``."""
        if self._client is None:
            raise RuntimeError("Twitch client is not available")
        channel = self._client.create_partialuser(channel_id)
        await channel.send_message(content, sender=self._client.bot_id)
