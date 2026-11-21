"""Thin Twitch service surfaced to modules.

The concrete client is bound by the Application at startup. Modules call
this service rather than reaching into the TwitchIO bot directly.
"""

from __future__ import annotations

from typing import Any


class TwitchService:
    def __init__(self) -> None:
        self._client: Any = None

    def bind(self, client: Any) -> None:
        self._client = client

    async def send_message(self, channel: str, content: str) -> None:
        if self._client is None:
            raise RuntimeError("Twitch client is not available")
        await self._client.send_message(channel, content)
