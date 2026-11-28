"""Thin Twitch service surfaced to modules.

The concrete client is bound by the Application at startup. Modules call
this service rather than reaching into the TwitchIO bot directly.

All sends are submitted to the shared chat queue so they respect Twitch's
account-wide rate limit, and use the same app-token-first / user-token
fallback as command replies. That matters for passive modules (the pyramid
watcher) which have no ``CommandContext.reply`` to use.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.core.chat_send import send_with_token_fallback
from bot.core.chat_send_queue import ChatSender, DirectChatSender

log = logging.getLogger(__name__)


class TwitchService:
    def __init__(self) -> None:
        self._client: Any = None
        self._sender: ChatSender = DirectChatSender()
        self._output_gate: Any = None
        self._fallback_warned_channels: set[str] = set()

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
        bot_id = str(getattr(client, "bot_id", "") or "")

        def has_user_token() -> bool:
            try:
                return bot_id in (getattr(client, "tokens", {}) or {})
            except TypeError:
                # A bare mock or a client without a token mapping.
                return False

        def authorization_url() -> str:
            maker = getattr(client, "channel_authorization_url", None)
            if not callable(maker):
                return ""
            return str(maker() or "")

        def channel_label() -> str:
            lookup = getattr(client, "_login_for", None)
            if not callable(lookup):
                return channel_id
            return str(lookup(channel_id) or channel_id)

        def note_fallback() -> None:
            if channel_id in self._fallback_warned_channels:
                return
            self._fallback_warned_channels.add(channel_id)
            log.info(
                "Channel %s is using the bot user token for sends "
                "(no Chat Bot badge until authorized): %s",
                channel_label(),
                authorization_url(),
            )

        async def deliver(text: str, token_for: str | None) -> None:
            channel = client.create_partialuser(channel_id)
            kwargs: dict[str, Any] = {"sender": client.bot_id}
            if token_for is not None:
                kwargs["token_for"] = token_for
            await channel.send_message(text, **kwargs)

        async def attempt_send(text: str) -> None:
            await send_with_token_fallback(
                lambda token: deliver(text, token),
                bot_id=bot_id,
                has_user_token=has_user_token,
                on_no_token=lambda: log.warning(
                    "Chat sends unauthorized in channel %s and no bot user token "
                    "is available; have the broadcaster authorize: %s",
                    channel_id,
                    authorization_url(),
                ),
                on_fallback=note_fallback,
                on_fallback_error=lambda exc: log.warning(
                    "Fallback user-token send failed in channel %s (%s)",
                    channel_id,
                    type(exc).__name__,
                ),
            )

        await self._sender.submit(attempt_send, content, channel_id)
