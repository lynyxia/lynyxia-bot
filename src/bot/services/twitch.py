"""Thin Twitch service surfaced to modules.

The concrete client is bound by the Application at startup. Modules call
this service rather than reaching into the TwitchIO bot directly.

All sends are submitted to the shared chat queue so they respect Twitch's
account-wide rate limit.
"""

from __future__ import annotations

from typing import Any

from bot.core.chat_send_queue import ChatSender, DirectChatSender


class TwitchService:
    def __init__(self) -> None:
        self._client: Any = None
        self._sender: ChatSender = DirectChatSender()

    def bind(self, client: Any, sender: ChatSender | None = None) -> None:
        self._client = client
        if sender is not None:
            self._sender = sender

    async def send_message(self, channel_id: str, content: str) -> None:
        """Queue ``content`` for the channel identified by ``channel_id``."""
        if self._client is None:
            raise RuntimeError("Twitch client is not available")

        client = self._client

        async def deliver(text: str) -> None:
            channel = client.create_partialuser(channel_id)
            await channel.send_message(text, sender=client.bot_id)

        await self._sender.submit(deliver, content, channel_id)
