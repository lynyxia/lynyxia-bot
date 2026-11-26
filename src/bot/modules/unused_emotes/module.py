"""``!unusedemotes``: build a 7TV unused-emotes HTML report per channel.

Only bot admins and the channel owner may run it (others are ignored). The
report is written to ``reports/<channel-login>/unusedemotes.html`` on a host
volume; the chat reply uses a public URL prefix from the module config.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

import httpx

from bot.core.event_router import CommandContext
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.modules.base import Module, ModuleContext
from bot.modules.unused_emotes.config import UnusedEmotesConfig
from bot.modules.unused_emotes.report import (
    MAX_EMOTE_NAME_LENGTH,
    build_patterns,
    parse_cutoff,
    parse_threshold,
    render_report,
)
from bot.modules.unused_emotes.seven_tv import SevenTVClient

USAGE = "Usage: !unusedemotes [cutoff] [threshold] (e.g. 6m 10)."
SAFE_LOGIN = re.compile(r"[A-Za-z0-9_]{1,32}")
REPORT_MESSAGE = "Report generated, view at {url}/{channel}/unusedemotes.html"
MAX_CONCURRENT_COUNTS = 8


class UnusedEmotesModule(Module):
    name = "unused_emotes"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = UnusedEmotesConfig.model_validate(context.config)
        self._messages: ChatMessageRepository | None = None
        self._http: SevenTVClient | None = None
        self._client: httpx.AsyncClient | None = None
        self._inflight: set[str] = set()
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        self._messages = ChatMessageRepository(self.context.services.database)
        self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        self._http = SevenTVClient(
            self.config.api_base, self.config.timeout_seconds, client=self._client
        )
        self.subscribe(
            self.context.router.on_command("unusedemotes", self.on_unusedemotes)
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().stop()

    def _login_for(self, channel_id: str) -> str | None:
        twitch = self.context.settings.twitch
        for known_id, login in zip(
            twitch.channel_id, twitch.channel, strict=False
        ):
            if known_id == channel_id:
                return login
        return None

    async def on_unusedemotes(self, ctx: CommandContext) -> None:
        permissions = self.context.services.permissions
        if permissions is None or not permissions.can_manage_channel(ctx):
            # Stay quiet for unauthorized users, like the other control commands.
            self.log.info(
                "Ignoring !unusedemotes from unauthorized user %s in channel %s",
                ctx.author_id,
                ctx.channel_id,
            )
            return

        if not self.config.url_prefix:
            await ctx.reply("Report URL is not configured.")
            return

        if len(ctx.args) > 2:
            await ctx.reply(USAGE)
            return

        cutoff_arg = ctx.args[0] if ctx.args else self.config.default_cutoff
        threshold_arg = (
            ctx.args[1] if len(ctx.args) > 1 else str(self.config.default_threshold)
        )
        try:
            since = parse_cutoff(cutoff_arg)
            threshold = parse_threshold(
                threshold_arg,
                default=self.config.default_threshold,
                max_threshold=self.config.max_threshold,
            )
        except ValueError:
            await ctx.reply(USAGE)
            return

        login = self._login_for(ctx.channel_id)
        if login is None or SAFE_LOGIN.fullmatch(login) is None:
            await ctx.reply("No report channel is configured for this channel ID.")
            return

        async with self._lock:
            if ctx.channel_id in self._inflight:
                await ctx.reply(
                    "A report is already being generated for this channel."
                )
                return
            self._inflight.add(ctx.channel_id)

        try:
            await ctx.reply(
                "Generating report. This will take a while, please wait..."
            )
            await self._generate(ctx, login, since, threshold)
        except Exception:
            self.log.exception(
                "Could not generate unused emotes report for channel %s",
                ctx.channel_id,
            )
            await ctx.reply("Error generating report.")
        finally:
            async with self._lock:
                self._inflight.discard(ctx.channel_id)

    async def _generate(
        self, ctx: CommandContext, login: str, since: datetime, threshold: int
    ) -> None:
        assert self._http is not None and self._messages is not None
        emote_set_id = await self._http.emote_set_id(ctx.channel_id)
        if not emote_set_id:
            await ctx.reply("No 7TV emote set found for this channel.")
            return

        emotes = await self._http.active_emotes(emote_set_id)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_COUNTS)

        async def count_emote(emote: dict) -> tuple[dict, str, int] | None:
            name = str(emote.get("name") or "").strip()
            if not name or len(name) > MAX_EMOTE_NAME_LENGTH:
                return None
            pattern, exclude_pattern = build_patterns(name)
            async with semaphore:
                total = await self._messages.count_emote(  # type: ignore[union-attr]
                    ctx.channel_id, pattern, exclude_pattern, since
                )
            if total < threshold:
                return emote, name, int(total)
            return None

        results = await asyncio.gather(*(count_emote(emote) for emote in emotes))
        under_threshold = sorted(
            (result for result in results if result is not None),
            key=lambda result: (-result[2], result[1]),
        )
        report = render_report(
            under_threshold, len(emotes), since, threshold, login
        )
        await asyncio.to_thread(self._write_report, login, report)

        await ctx.reply(
            REPORT_MESSAGE.format(url=self.config.url_prefix, channel=login)
        )

    def _write_report(self, login: str, report: str) -> None:
        directory = Path(self.config.reports_dir) / login
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / self.config.filename
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(target)
