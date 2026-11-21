"""Application composition root.

Wires configuration, services, the event router, the module manager and the
Twitch client together and owns their lifecycle.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from bot.config import Settings
from bot.core.event_router import EventRouter
from bot.core.module_manager import ModuleManager
from bot.core.permissions import PermissionService
from bot.services import Services
from bot.services.database import DatabaseService
from bot.services.twitch import TwitchService
from bot.utils.logging import configure_logging

log = logging.getLogger(__name__)

BotFactory = Callable[[], Any]


class Application:
    def __init__(
        self,
        settings: Settings,
        *,
        bot: Any | None = None,
        database: Any | None = None,
    ) -> None:
        configure_logging(settings.logging.level)
        self.settings = settings
        self.router = EventRouter()
        self.database = database if database is not None else DatabaseService(settings.database)
        self.twitch = TwitchService()
        self.permissions = PermissionService(settings.admin_user_ids)
        self.services = Services(
            database=self.database,
            twitch=self.twitch,
            llm=None,
            permissions=self.permissions,
        )
        self.modules = ModuleManager(settings, self.router, self.services)

        self._bot = bot
        self._bot_task: asyncio.Task[None] | None = None

    def _create_bot(self) -> Any:
        # Imported lazily so unit tests don't need a TwitchIO event loop.
        from bot.core.bot import BotClient

        twitch = self.settings.twitch
        return BotClient(
            router=self.router,
            token=twitch.oauth_token.get_secret_value(),
            client_id=twitch.client_id,
            nick=twitch.bot_username,
            prefix="!",
            initial_channels=[twitch.channel],
        )

    async def start(self) -> None:
        await self.database.connect()
        await self.modules.load()
        await self.modules.start_all()

        if self._bot is None:
            self._bot = self._create_bot()
        elif hasattr(self._bot, "router"):
            self._bot.router = self.router

        self.twitch.bind(self._bot)
        self._bot_task = asyncio.create_task(self._run_bot())
        log.info("Application started")

    async def _run_bot(self) -> None:
        start = getattr(self._bot, "start", None)
        if start is None:
            return
        try:
            result: Awaitable[None] | None = start()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Twitch client stopped unexpectedly")

    @property
    def bot_task(self) -> asyncio.Task[None] | None:
        return self._bot_task

    async def stop(self) -> None:
        close = getattr(self._bot, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

        if self._bot_task is not None:
            self._bot_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._bot_task
            self._bot_task = None

        await self.modules.stop_all()
        await self.database.dispose()
        log.info("Application stopped")

    async def __aenter__(self) -> Application:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()
