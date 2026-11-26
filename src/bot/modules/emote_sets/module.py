"""``!emotesets``: compare a channel's 7TV emote sets in an HTML report.

Admin/owner-only, like ``!unusedemotes``. Shared report settings, the 7TV
client, safe paths, in-flight deduplication and HTML escaping all come from the
shared services, so this module never imports another module.
"""

from __future__ import annotations

import asyncio

import httpx

from bot.core.event_router import CommandContext
from bot.modules.base import Module, ModuleContext
from bot.modules.emote_sets.compare import EmoteSet, build_comparisons, render_report
from bot.modules.emote_sets.config import EmoteSetsConfig
from bot.services.reporting import (
    ReportCoordinator,
    channel_login,
    is_report_admin,
    report_link,
    write_report,
)
from bot.services.seven_tv import SevenTVClient

USAGE = "Usage: !emotesets (no arguments)."
MAX_CONCURRENT_FETCHES = 8


class EmoteSetsModule(Module):
    name = "emote_sets"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = EmoteSetsConfig.model_validate(context.config)
        self._reports = context.settings.reports
        self._http: SevenTVClient | None = None
        self._client: httpx.AsyncClient | None = None
        self._coordinator = ReportCoordinator()

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._reports.timeout_seconds)
        self._http = SevenTVClient(
            self._reports.api_base, self._reports.timeout_seconds, client=self._client
        )
        self.subscribe(self.context.router.on_command("emotesets", self.on_emotesets))

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().stop()

    async def on_emotesets(self, ctx: CommandContext) -> None:
        if not is_report_admin(self.context.services.permissions, ctx):
            self.log.info(
                "Ignoring !emotesets from unauthorized user %s in channel %s",
                ctx.author_id,
                ctx.channel_id,
            )
            return

        if not self._reports.url_prefix:
            await ctx.reply("Report URL is not configured.")
            return
        if ctx.args:
            await ctx.reply(USAGE)
            return

        login = channel_login(self.context.settings, ctx.channel_id)
        if login is None:
            await ctx.reply("No report channel is configured for this channel ID.")
            return

        # Key includes the report type so the two report commands can run in
        # parallel in the same channel.
        key = f"emotesets:{ctx.channel_id}"
        if not await self._coordinator.claim(key):
            await ctx.reply("A report is already being generated for this channel.")
            return

        try:
            await ctx.reply("Generating report, please wait...")
            await self._generate(ctx, login)
        except Exception:
            self.log.exception(
                "Could not generate emote set report for channel %s", ctx.channel_id
            )
            await ctx.reply("Error generating report.")
        finally:
            await self._coordinator.release(key)

    async def _generate(self, ctx: CommandContext, login: str) -> None:
        assert self._http is not None
        owned = await self._http.owned_emote_sets(ctx.channel_id)
        if len(owned) < 2:
            await ctx.reply("Fewer than two 7TV emote sets found.")
            return

        owned.sort(key=lambda item: item["name"].casefold())
        truncated = len(owned) > self.config.max_sets
        selected = owned[: self.config.max_sets]
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

        async def fetch(item: dict) -> EmoteSet:
            async with semaphore:
                full = await self._http.emote_set(item["id"])  # type: ignore[union-attr]
            return EmoteSet(
                id=item["id"],
                name=item["name"],
                emotes=full.get("emotes") or [],
            )

        sets = await asyncio.gather(*(fetch(item) for item in selected))
        comparisons = build_comparisons(sets)
        report = render_report(comparisons, sets, login, truncated)
        await asyncio.to_thread(
            write_report, self._reports.reports_dir, login, self.config.filename, report
        )

        link = report_link(self._reports.url_prefix, login, self.config.filename)
        await ctx.reply(f"Report generated, view at {link}")
