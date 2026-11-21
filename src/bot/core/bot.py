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
from typing import Any
from urllib.parse import urlencode

from aiohttp import web
from twitchio import Scopes
from twitchio.eventsub import ChatMessageSubscription
from twitchio.ext import commands
from twitchio.web import AiohttpAdapter

from bot.core.event_router import CommandContext, EventRouter
from bot.database.repositories.oauth_token import OAuthTokenRepository

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
        channel_ids: list[str],
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        token_store: OAuthTokenRepository,
        **kwargs: Any,
    ) -> None:
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scopes = list(scopes)
        super().__init__(
            client_id=client_id,
            bot_id=bot_id,
            redirect_uri=redirect_uri,
            scopes=Scopes(self._scopes),
            **kwargs,
        )
        self.router = router
        self.channel_ids = list(channel_ids)
        self._token_store = token_store
        self._chat_subscribed = False
        self._subscribe_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # OAuth token lifecycle
    # ------------------------------------------------------------------
    @property
    def authorization_url(self) -> str:
        """One-time URL for authorizing the bot account."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
        }
        return "https://id.twitch.tv/oauth2/authorize?" + urlencode(params)

    async def load_tokens(self, path: str | None = None) -> None:
        """Load the bot token from PostgreSQL instead of a JSON file."""
        record = await self._token_store.get(self.bot_id)
        if record is None:
            log.info("No stored Twitch token for bot user %s", self.bot_id)
            return
        try:
            validated = await self.add_token(record.access_token, record.refresh_token)
        except Exception:
            # TwitchIO token errors can contain the raw token; never log them.
            log.error(
                "Stored Twitch token for bot user %s could not be validated; "
                "authorization is required",
                self.bot_id,
            )
            return
        if str(getattr(validated, "user_id", "") or "") != self.bot_id:
            log.error(
                "Stored Twitch token belongs to a different user; authorization is required"
            )
            return
        log.info("Loaded stored Twitch token for bot user %s", self.bot_id)

    async def save_tokens(self, path: str | None = None) -> None:
        """Persist the current token pair on shutdown."""
        await self._persist_token()

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

    async def event_oauth_authorized(self, payload: Any) -> None:
        """Called by TwitchIO after the browser authorization callback."""
        user_id = str(getattr(payload, "user_id", "") or "")
        if user_id != self.bot_id:
            log.error(
                "Authorization completed by user %s; expected bot user %s",
                user_id,
                self.bot_id,
            )
            return
        try:
            await self.add_token(payload.access_token, payload.refresh_token)
        except Exception:
            log.error("Failed to register the authorized Twitch token")
            return
        await self._persist_token()
        await self._subscribe_chat()
        log.info("Twitch authorization completed for bot user %s", self.bot_id)

    async def event_token_refreshed(self, payload: Any) -> None:
        """Persist rotated refresh tokens so restarts keep working."""
        if str(getattr(payload, "user_id", "") or "") != self.bot_id:
            return
        await self._persist_token()

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

    async def setup_hook(self) -> None:
        """Subscribe if a token exists, otherwise request authorization."""
        if self.bot_id in self.tokens:
            await self._subscribe_chat()
        else:
            log.warning(
                "Twitch authorization required. Open this URL once and log in as "
                "the bot account: %s",
                self.authorization_url,
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

        await self.router.dispatch_event("message", message)

        content = getattr(message, "text", None) or getattr(message, "content", "") or ""
        if not content.startswith("!"):
            return
        parts = content[1:].split()
        if not parts:
            return

        # The broadcaster identifies which of the configured channels this
        # message came from.
        broadcaster = getattr(message, "broadcaster", None) or getattr(
            message, "channel", None
        )

        async def reply(text: str) -> None:
            responder = getattr(message, "respond", None)
            if responder is not None:
                await responder(text)
                return
            if broadcaster is not None and hasattr(broadcaster, "send"):
                await broadcaster.send(text)

        ctx = CommandContext(
            command=parts[0].lower(),
            args=parts[1:],
            author_id=str(getattr(chatter, "id", "") or ""),
            author_name=str(
                getattr(chatter, "name", None)
                or getattr(chatter, "display_name", "")
                or ""
            ),
            channel_id=str(getattr(broadcaster, "id", "") or ""),
            channel_name=str(
                getattr(broadcaster, "name", None)
                or getattr(broadcaster, "display_name", "")
                or ""
            ),
            content=content,
            reply=reply,
            raw=message,
        )
        await self.router.dispatch_command(ctx)
