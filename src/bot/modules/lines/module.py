"""Message counter: ``!lines <username|id>``.

Reports how many messages a user has sent in the current channel and when their
first message was posted.
"""

from __future__ import annotations

from bot.core.event_router import CommandContext
from bot.database.models import User
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.database.repositories.user import UserRepository
from bot.modules.base import Module, ModuleContext
from bot.modules.lines.config import LinesConfig
from bot.utils.time import as_utc

USAGE = "Usage: !lines <username>"
LOOKUP_ERROR = "Error searching logs."


async def _respond(ctx: CommandContext, text: str) -> None:
    """Reply in the thread of the command message."""
    responder = ctx.reply_threaded or ctx.reply
    await responder(text)


class LinesModule(Module):
    name = "lines"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = LinesConfig.model_validate(context.config)
        self._messages: ChatMessageRepository | None = None
        self._users: UserRepository | None = None

    async def setup(self) -> None:
        database = self.context.services.database
        self._messages = ChatMessageRepository(database)
        self._users = UserRepository(database)
        self.subscribe(self.context.router.on_command("lines", self.on_lines))

    async def _resolve_user(self, target: str) -> User | None:
        assert self._users is not None
        if target.isdigit():
            user = await self._users.get_by_id(target)
            if user is not None:
                return user
        return await self._users.get_by_login(target)

    async def on_lines(self, ctx: CommandContext) -> None:
        target = ctx.args[0].strip().removeprefix("@") if ctx.args else ""
        if not target:
            await _respond(ctx, USAGE)
            return

        try:
            user = await self._resolve_user(target)
            if user is None:
                await _respond(ctx, f"No messages found for user '{target}'.")
                return
            assert self._messages is not None
            count, first_at = await self._messages.count_and_first(
                ctx.channel_id, user.id
            )
        except Exception as exc:
            self.log.warning("Line count failed (%s)", type(exc).__name__)
            await _respond(ctx, LOOKUP_ERROR)
            return

        if count == 0:
            await _respond(ctx, f"No messages found for user '{target}'.")
            return

        first = as_utc(first_at)
        since = first.strftime(self.config.date_format) if first else "unknown"
        await _respond(ctx, f"{user.login} has sent {count:,} messages since {since}.")
