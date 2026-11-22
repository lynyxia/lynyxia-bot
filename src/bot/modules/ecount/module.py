"""Emote counter: ``!ecount <emote> [timeframe]``.

Counts messages containing an emote in the current channel over a configurable
window. Timeframes use the legacy ``<number><y|w|d|m>`` syntax (e.g. ``6m``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from bot.core.event_router import CommandContext
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.modules.base import Module, ModuleContext
from bot.modules.ecount.config import EcountConfig

# Emote names are kept to the characters Twitch/BTTV/FFZ actually use. This also
# prevents regex injection and pathological patterns from reaching PostgreSQL.
EMOTE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.]{1,64}")
TIMEFRAME_PATTERN = re.compile(r"(\d+)([ywdm])", re.IGNORECASE)
WORD_BOUNDARY = r"[a-zA-Z0-9_.]"
UNIT_DAYS = {"y": 365, "m": 30, "w": 7, "d": 1}

USAGE = "Usage: !ecount <emote> [timeframe] (e.g. 6m, 2d, 1w, 1y)."
INVALID_EMOTE = "Invalid emote name."
NO_RESULTS = "No results found."
LOOKUP_ERROR = "Error searching logs."


def parse_timeframe(value: str, *, default_days: int, max_days: int) -> int:
    """Return the window in days; invalid or oversized values fall back/clamp."""
    match = TIMEFRAME_PATTERN.fullmatch(value)
    if match is None:
        return min(default_days, max_days)
    days = int(match.group(1)) * UNIT_DAYS[match.group(2).lower()]
    return max(1, min(days, max_days))


def build_patterns(emote: str) -> tuple[str, str]:
    """Build the emote and command-exclusion regexes for PostgreSQL ``~``."""
    escaped = re.escape(emote)
    pattern = rf"(?<!{WORD_BOUNDARY}){escaped}(?!{WORD_BOUNDARY})"
    exclude_pattern = rf"!ecount\s+{escaped}"
    return pattern, exclude_pattern


async def _respond(ctx: CommandContext, text: str) -> None:
    """Reply in the thread of the command message."""
    responder = ctx.reply_threaded or ctx.reply
    await responder(text)


class EcountModule(Module):
    name = "ecount"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = EcountConfig.model_validate(context.config)
        self._messages: ChatMessageRepository | None = None

    async def setup(self) -> None:
        self._messages = ChatMessageRepository(self.context.services.database)
        self.subscribe(self.context.router.on_command("ecount", self.on_ecount))

    async def on_ecount(self, ctx: CommandContext) -> None:
        if not ctx.args:
            await _respond(ctx, USAGE)
            return

        emote = ctx.args[0].strip().removeprefix("@")
        if EMOTE_NAME_PATTERN.fullmatch(emote) is None:
            await _respond(ctx, INVALID_EMOTE)
            return

        days = parse_timeframe(
            ctx.args[1] if len(ctx.args) > 1 else "",
            default_days=self.config.default_days,
            max_days=self.config.max_days,
        )
        since = datetime.now(UTC) - timedelta(days=days)
        pattern, exclude_pattern = build_patterns(emote)

        try:
            assert self._messages is not None
            total = await self._messages.count_emote(
                ctx.channel_id, pattern, exclude_pattern, since
            )
        except Exception as exc:
            self.log.warning("Emote count failed (%s)", type(exc).__name__)
            await _respond(ctx, LOOKUP_ERROR)
            return

        if total == 0:
            await _respond(ctx, NO_RESULTS)
            return

        noun = "time" if total == 1 else "times"
        await _respond(ctx, f"{emote} was used {total:,} {noun}.")
