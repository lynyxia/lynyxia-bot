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
        self._output_gate: Any = None

    def bind(
        self,
        client: Any,
        sender: ChatSender | None = None,
        output_gate: Any = None,
    ) -> None:
        self._client = client
        if sender is not None:
            self._sender = sender
        if output_gate is not None:
            self._output_gate = output_gate

    async def send_message(
        self, channel_id: str, content: str, *, force: bool = False
    ) -> None:
        """Queue ``content`` for the channel identified by ``channel_id``."""
        if (
            not force
            and self._output_gate is not None
            and not await self._output_gate.is_enabled(channel_id)
        ):
            return
        if self._client is None:
            raise RuntimeError("Twitch client is not available")

        client = self._client

        async def deliver(text: str) -> None:
            channel = client.create_partialuser(channel_id)
            await channel.send_message(text, sender=client.bot_id)

        await self._sender.submit(deliver, content, channel_id)
