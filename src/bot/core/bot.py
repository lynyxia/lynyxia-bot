"""Twitch client integration.

The core bot is the only place coupled to TwitchIO. It converts incoming
Twitch events into calls on the framework's ``EventRouter`` so modules stay
independent of TwitchIO's API.

TwitchIO 3.x delivers chat over EventSub, not IRC. The bot manages its own
user token: it loads the token from PostgreSQL, lets TwitchIO refresh it, and
persists rotations back. On first run it logs an authorization URL and waits
for the built-in OAuth callback. Subscriptions are activated once a token is
available.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

from aiohttp import web
from twitchio import Scopes
from twitchio.eventsub import (
    ChatMessageSubscription,
    StreamOfflineSubscription,
    StreamOnlineSubscription,
)
from twitchio.ext import commands
from twitchio.web import AiohttpAdapter

from bot.core.chat_send_queue import ChatSender, DirectChatSender
from bot.core.event_router import ChatMessageEvent, CommandContext, EventRouter
from bot.database.repositories.channel_authorization import (
    ChannelAuthorizationRepository,
)
from bot.database.repositories.oauth_token import OAuthTokenRepository
from bot.utils.time import as_utc

log = logging.getLogger(__name__)


class BotAdapter(AiohttpAdapter):
    """TwitchIO's aiohttp adapter without its own signal handlers.

    Application shutdown owns SIGINT/SIGTERM; letting aiohttp replace those
    handlers would break graceful shutdown.
    """

    async def run(self, host: str | None = None, port: int | None = None) -> None:
        self._running = True
        self._runner = web.AppRunner(self, access_log=None, handle_signals=False)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            host or self._host,
            port or self._port,
            ssl_context=self._ssl_context,
        )
        self._runner_task = asyncio.create_task(
            site.start(), name=f"twitchio-web-adapter:{type(self).__qualname__}"
        )
        self._runner_task.add_done_callback(self._task_callback)


class BotClient(commands.Bot):
    def __init__(
        self,
        *,
        router: EventRouter,
        bot_id: str,
        bot_login: str,
        channel_ids: list[str],
        channel_logins: list[str],
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        channel_scopes: list[str],
        token_store: OAuthTokenRepository,
        channel_token_store: ChannelAuthorizationRepository,
        chat_sender: ChatSender | None = None,
        output_gate: Any | None = None,
        live_status: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scopes = list(scopes)
        self._channel_scopes = list(channel_scopes)
        super().__init__(
            client_id=client_id,
            bot_id=bot_id,
            redirect_uri=redirect_uri,
            scopes=Scopes(self._scopes),
            **kwargs,
        )
        self.router = router
        self._bot_login = bot_login
        self.channel_ids = list(channel_ids)
        self.channel_logins = list(channel_logins)
        self._token_store = token_store
        self._channel_token_store = channel_token_store
        self._chat_sender = chat_sender or DirectChatSender()
        self._output_gate = output_gate
        self._live_status = live_status
        self._authorized_channels: set[str] = set()
        self._warned_channels: set[str] = set()
        self._fallback_warned_channels: set[str] = set()
        self._chat_subscribed = False
        self._stream_subscribed = False
        self._subscribe_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # OAuth token lifecycle
    # ------------------------------------------------------------------
    def _authorization_url(self, scopes: Sequence[str]) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
        }
        return "https://id.twitch.tv/oauth2/authorize?" + urlencode(params)

    @property
    def authorization_url(self) -> str:
        """One-time URL for authorizing the bot account."""
        return self._authorization_url(self._scopes)

    def channel_authorization_url(self) -> str:
        """URL a broadcaster opens to grant ``channel:bot`` to the app."""
        return self._authorization_url(self._channel_scopes)

    def _login_for(self, channel_id: str) -> str:
        for known_id, login in zip(self.channel_ids, self.channel_logins, strict=False):
            if known_id == channel_id:
                return login
        return channel_id

    def _activation_text(self, content: str, message: Any) -> str:
        """Return the start of what the chatter actually typed.

        Twitch prepends an ``@`` mention of the thread/parent user to replies in
        chat. That prefix is generated by Twitch, not typed by the chatter, so
        it must be skipped when deciding whether the message *starts* with a
        mention of the bot.
        """
        text = content.lstrip()
        if getattr(message, "reply", None) is None:
            return text

        # Twitch prepends an @mention of the thread/parent user to every reply.
        # It is not part of what the chatter typed, so drop it. Relying on the
        # reply object's user fields is unreliable; the first @token is enough.
        prefix = re.match(r"@([A-Za-z0-9_]+)\b\s*", text)
        if prefix is None or prefix.group(1).lower() == (self._bot_login or "").lower():
            return text
        return text[prefix.end() :].lstrip()

    def _starts_with_bot_mention(self, content: str) -> bool:
        """Only a leading ``@botname`` activates the bot, not a mid-message mention."""
        if not self._bot_login:
            return False
        return (
            re.match(
                rf"@({re.escape(self._bot_login)})\b",
                content,
                flags=re.IGNORECASE,
            )
            is not None
        )

    async def load_tokens(self, path: str | None = None) -> None:
        """Load bot and per-channel tokens from PostgreSQL instead of a file."""
        record = await self._token_store.get(self.bot_id)
        if record is None:
            log.info("No stored Twitch token for bot user %s", self.bot_id)
        else:
            try:
                validated = await self.add_token(record.access_token, record.refresh_token)
            except Exception:
                # TwitchIO token errors can contain the raw token; never log them.
                log.error(
                    "Stored Twitch token for bot user %s could not be validated; "
                    "authorization is required",
                    self.bot_id,
                )
            else:
                if str(getattr(validated, "user_id", "") or "") == self.bot_id:
                    log.info("Loaded stored Twitch token for bot user %s", self.bot_id)
                else:
                    log.error(
                        "Stored Twitch token belongs to a different user; "
                        "authorization is required"
                    )

        for channel in await self._channel_token_store.list_all():
            try:
                validated = await self.add_token(
                    channel.access_token, channel.refresh_token
                )
            except Exception:
                log.error(
                    "Stored authorization for channel %s could not be validated",
                    channel.channel_id,
                )
                continue
            if str(getattr(validated, "user_id", "") or "") != channel.channel_id:
                log.error(
                    "Stored authorization for channel %s belongs to a different user",
                    channel.channel_id,
                )
                continue
            self._authorized_channels.add(channel.channel_id)

    async def save_tokens(self, path: str | None = None) -> None:
        """Persist the current token pairs on shutdown."""
        await self._persist_token()
        for channel_id in self._authorized_channels:
            await self._persist_channel_token(channel_id)

    async def _persist_token(self) -> None:
        entry = self.tokens.get(self.bot_id)
        if entry is None:
            return
        try:
            await self._token_store.save(
                self.bot_id,
                entry["token"],
                entry["refresh"],
                " ".join(self._scopes),
            )
        except Exception:
            log.error("Failed to persist Twitch OAuth token for bot user %s", self.bot_id)

    async def _persist_channel_token(self, channel_id: str) -> None:
        entry = self.tokens.get(channel_id)
        if entry is None:
            return
        try:
            await self._channel_token_store.save(
                channel_id,
                self._login_for(channel_id),
                entry["token"],
                entry["refresh"],
                " ".join(self._channel_scopes),
            )
        except Exception:
            log.error("Failed to persist authorization for channel %s", channel_id)

    async def event_oauth_authorized(self, payload: Any) -> None:
        """Route the callback to the bot account or a broadcaster."""
        user_id = str(getattr(payload, "user_id", "") or "")

        if user_id == self.bot_id:
            try:
                await self.add_token(payload.access_token, payload.refresh_token)
            except Exception:
                log.error("Failed to register the authorized Twitch token")
                return
            await self._persist_token()
            await self._subscribe_chat()
            await self._subscribe_stream_status()
            await self._hydrate_live_status()
            log.info("Twitch authorization completed for bot user %s", self.bot_id)
            return

        if user_id in self.channel_ids:
            try:
                await self.add_token(payload.access_token, payload.refresh_token)
            except Exception:
                log.error("Failed to register authorization for channel %s", user_id)
                return
            await self._persist_channel_token(user_id)
            self._authorized_channels.add(user_id)
            self._warned_channels.discard(user_id)
            log.info(
                "Channel %s authorized the app for chat sends",
                self._login_for(user_id),
            )
            return

        log.error("Authorization completed by unexpected user %s", user_id)

    async def event_token_refreshed(self, payload: Any) -> None:
        """Persist rotated refresh tokens so restarts keep working."""
        user_id = str(getattr(payload, "user_id", "") or "")
        if user_id == self.bot_id:
            await self._persist_token()
        elif user_id in self.channel_ids:
            await self._persist_channel_token(user_id)

    # ------------------------------------------------------------------
    # EventSub and event routing
    # ------------------------------------------------------------------
    async def _subscribe_chat(self) -> None:
        if self._chat_subscribed:
            return
        async with self._subscribe_lock:
            if self._chat_subscribed:
                return
            for channel_id in self.channel_ids:
                try:
                    await self.subscribe_websocket(
                        ChatMessageSubscription(
                            broadcaster_user_id=channel_id,
                            user_id=self.bot_id,
                        )
                    )
                except Exception:
                    log.error(
                        "Failed to subscribe to chat events for channel %s", channel_id
                    )
                    return
            self._chat_subscribed = True
            log.info(
                "Subscribed to %s channel(s): %s", len(self.channel_ids), self.channel_ids
            )

    async def _subscribe_stream_status(self) -> None:
        """Track live/offline transitions for every configured channel."""
        if self._stream_subscribed or self._live_status is None:
            return
        async with self._subscribe_lock:
            if self._stream_subscribed:
                return
            for channel_id in self.channel_ids:
                for subscription in (
                    StreamOnlineSubscription(broadcaster_user_id=channel_id),
                    StreamOfflineSubscription(broadcaster_user_id=channel_id),
                ):
                    try:
                        await self.subscribe_websocket(subscription)
                    except Exception:
                        log.error(
                            "Failed to subscribe to stream status for channel %s",
                            channel_id,
                        )
                        return
            self._stream_subscribed = True
            log.info(
                "Subscribed to stream status for %s channel(s)", len(self.channel_ids)
            )

    async def _hydrate_live_status(self) -> None:
        """Fetch the current stream state; EventSub only reports transitions."""
        if self._live_status is not None:
            await self._live_status.hydrate(self.http, self.channel_ids)

    async def event_stream_online(self, payload: Any) -> None:
        if self._live_status is None:
            return
        channel_id = str(getattr(getattr(payload, "broadcaster", None), "id", "") or "")
        if channel_id:
            self._live_status.set_live(channel_id, True)

    async def event_stream_offline(self, payload: Any) -> None:
        if self._live_status is None:
            return
        channel_id = str(getattr(getattr(payload, "broadcaster", None), "id", "") or "")
        if channel_id:
            self._live_status.set_live(channel_id, False)

    async def setup_hook(self) -> None:
        """Subscribe if a token exists, otherwise request authorization."""
        if self.bot_id in self.tokens:
            await self._subscribe_chat()
            await self._subscribe_stream_status()
            await self._hydrate_live_status()
        else:
            log.warning(
                "Twitch authorization required. Open this URL once and log in as "
                "the bot account: %s",
                self.authorization_url,
            )

        for channel_id in self.channel_ids:
            if channel_id in self._authorized_channels:
                continue
            log.warning(
                "Channel %s (%s) must authorize the bot for chat sends (bot badge): %s",
                self._login_for(channel_id),
                channel_id,
                self.channel_authorization_url(),
            )

    async def event_ready(self) -> None:
        name = getattr(self.user, "name", None) or self.bot_id
        await self.router.dispatch_event("ready", name)

    async def event_message(self, message: Any) -> None:
        # TwitchIO 3's ChatMessage uses chatter/broadcaster/text instead of the
        # 2.x author/channel/content API. Fallbacks keep mocked messages simple.
        chatter = getattr(message, "chatter", None) or getattr(message, "author", None)
        if str(getattr(chatter, "id", "") or "") == self.bot_id:
            return
        if getattr(message, "echo", False):
            return

        # The broadcaster identifies which of the configured channels this
        # message came from.
        broadcaster = getattr(message, "broadcaster", None) or getattr(
            message, "channel", None
        )
        content = getattr(message, "text", None) or getattr(message, "content", "") or ""
        # Chatterino inserts U+E0000 into messages to get around Twitch's
        # unique-message limit. It has no meaning outside that, so strip every
        # occurrence here: this is the single chat-ingestion boundary, so no
        # module ever sees the character (events, commands or logs).
        content = str(content).replace("\U000e0000", "")

        sent_at = as_utc(getattr(message, "timestamp", None))

        event = ChatMessageEvent(
            author_id=str(getattr(chatter, "id", "") or ""),
            author_login=str(getattr(chatter, "name", "") or ""),
            author_name=str(
                getattr(chatter, "display_name", None) or getattr(chatter, "name", "") or ""
            ),
            channel_id=str(getattr(broadcaster, "id", "") or ""),
            channel_login=str(getattr(broadcaster, "name", "") or ""),
            channel_name=str(
                getattr(broadcaster, "display_name", None)
                or getattr(broadcaster, "name", "")
                or ""
            ),
            content=content,
            message_id=str(getattr(message, "id", "") or "") or None,
            sent_at=sent_at,
            raw=message,
        )
        await self.router.dispatch_event("message", event)

        reply_info = getattr(message, "reply", None)
        thread_id = (
            str(getattr(reply_info, "thread_message_id", "") or "")
            or str(getattr(reply_info, "parent_message_id", "") or "")
            or str(getattr(message, "id", "") or "")
            or None
        )

        mentions = getattr(message, "mentions", None) or ()
        log.info(
            "Chat message: channel=%s user=%s message_id=%s reply=%s thread_id=%s "
            "mentions=%s text=%r",
            event.channel_name,
            event.author_login,
            event.message_id,
            reply_info is not None,
            thread_id,
            [str(getattr(item, "id", "") or "") for item in mentions],
            content,
        )

        async def _send(
            text: str,
            *,
            reply_to_message_id: str | None = None,
            force: bool = False,
        ) -> None:
            """Queue a send; prefer the app token, fall back to the user token.

            Unless ``force`` is set, the per-channel output gate can silence the
            send. Only the control command uses ``force`` so an admin can always
            re-enable output in a channel they disabled it in.
            """
            channel_id = event.channel_id
            if not force and self._output_gate is not None:
                if not await self._output_gate.is_enabled(channel_id):
                    log.info(
                        "Output disabled in channel %s; dropping message",
                        event.channel_name,
                    )
                    return
            send = getattr(broadcaster, "send_message", None)

            async def deliver(content: str, *, token_for: str | None) -> None:
                if send is None:
                    responder = getattr(message, "respond", None)
                    if responder is not None:
                        await responder(content)
                    elif broadcaster is not None and hasattr(broadcaster, "send"):
                        await broadcaster.send(content)
                    return
                kwargs: dict[str, Any] = {"sender": self.bot_id}
                if token_for is not None:
                    kwargs["token_for"] = token_for
                if reply_to_message_id:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                await send(content, **kwargs)

            async def attempt_send(content: str) -> None:
                try:
                    await deliver(content, token_for=None)
                    return
                except Exception as exc:
                    if getattr(exc, "status", None) != 401:
                        log.warning(
                            "Chat send failed in channel %s (%s)",
                            channel_id,
                            type(exc).__name__,
                        )
                        return

                # 401: the app lacks channel:bot/mod status here. Use the bot
                # user token until the broadcaster authorizes.
                if self.bot_id not in self.tokens:
                    log.warning(
                        "Chat sends unauthorized in channel %s and no bot user token "
                        "is available; have the broadcaster authorize: %s",
                        channel_id,
                        self.channel_authorization_url(),
                    )
                    return

                try:
                    await deliver(content, token_for=self.bot_id)
                except Exception as exc:
                    log.warning(
                        "Fallback user-token send failed in channel %s (%s)",
                        channel_id,
                        type(exc).__name__,
                    )
                    return

                if channel_id not in self._fallback_warned_channels:
                    self._fallback_warned_channels.add(channel_id)
                    log.info(
                        "Channel %s is using the bot user token for sends "
                        "(no Chat Bot badge until authorized): %s",
                        self._login_for(channel_id),
                        self.channel_authorization_url(),
                    )

            await self._chat_sender.submit(attempt_send, text, channel_id)

        async def reply(text: str) -> None:
            await _send(text)

        async def reply_force(text: str) -> None:
            """Reply even when the channel has output disabled."""
            await _send(text, force=True)

        async def reply_threaded(text: str) -> None:
            """Answer inside the thread of the command message."""
            message_id = getattr(message, "id", None)
            await _send(text, reply_to_message_id=message_id)

        activation_text = self._activation_text(content, message)
        if self._starts_with_bot_mention(activation_text):
            mention_ctx = CommandContext(
                command="chat",
                args=activation_text.split(),
                author_id=event.author_id,
                author_name=event.author_name,
                author_login=event.author_login,
                channel_id=event.channel_id,
                channel_name=event.channel_name,
                content=activation_text,
                reply=reply,
                reply_force=reply_force,
                raw=message,
                reply_threaded=reply_threaded,
                thread_id=thread_id,
            )
            await self.router.dispatch_command(mention_ctx)
            return

        if not content.startswith("!"):
            return
        parts = content[1:].split()
        if not parts:
            return

        ctx = CommandContext(
            command=parts[0].lower(),
            args=parts[1:],
            author_id=event.author_id,
            author_name=event.author_name,
            author_login=event.author_login,
            channel_id=event.channel_id,
            channel_name=event.channel_name,
            content=content,
            reply=reply,
            reply_force=reply_force,
            raw=message,
            reply_threaded=reply_threaded,
            thread_id=thread_id,
        )
        await self.router.dispatch_command(ctx)
