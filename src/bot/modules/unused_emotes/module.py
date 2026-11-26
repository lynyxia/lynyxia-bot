"""``!unusedemotes``: build a 7TV unused-emotes HTML report per channel.

Only bot admins and the channel owner may run it (others are ignored). The
report is written to ``reports/<channel-login>/unusedemotes.html`` on a host
volume; the chat reply uses the public URL prefix from ``BOT_REPORTS``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

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
from bot.services.reporting import (
    ReportCoordinator,
    channel_login,
    is_report_admin,
    report_link,
    write_report,
)
from bot.services.seven_tv import SevenTVClient

USAGE = "Usage: !unusedemotes [cutoff] [threshold] (e.g. 6m 10)."
MAX_CONCURRENT_COUNTS = 8


class UnusedEmotesModule(Module):
    name = "unused_emotes"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = UnusedEmotesConfig.model_validate(context.config)
        self._reports = context.settings.reports
        self._messages: ChatMessageRepository | None = None
        self._http: SevenTVClient | None = None
        self._client: httpx.AsyncClient | None = None
        self._coordinator = ReportCoordinator()

    async def setup(self) -> None:
        self._messages = ChatMessageRepository(self.context.services.database)
        self._client = httpx.AsyncClient(timeout=self._reports.timeout_seconds)
        self._http = SevenTVClient(
            self._reports.api_base, self._reports.timeout_seconds, client=self._client
        )
        self.subscribe(
            self.context.router.on_command("unusedemotes", self.on_unusedemotes)
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().stop()

    async def on_unusedemotes(self, ctx: CommandContext) -> None:
        if not is_report_admin(self.context.services.permissions, ctx):
            # Stay quiet for unauthorized users, like the other control commands.
            self.log.info(
                "Ignoring !unusedemotes from unauthorized user %s in channel %s",
                ctx.author_id,
                ctx.channel_id,
            )
            return

        if not self._reports.url_prefix:
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

        login = channel_login(self.context.settings, ctx.channel_id)
        if login is None:
            await ctx.reply("No report channel is configured for this channel ID.")
            return

        key = f"unusedemotes:{ctx.channel_id}"
        if not await self._coordinator.claim(key):
            await ctx.reply("A report is already being generated for this channel.")
            return

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
            await self._coordinator.release(key)

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
        await asyncio.to_thread(
            write_report, self._reports.reports_dir, login, self.config.filename, report
        )

        link = report_link(self._reports.url_prefix, login, self.config.filename)
        await ctx.reply(f"Report generated, view at {link}")
