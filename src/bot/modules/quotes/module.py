"""Quotes module: ``!rq`` and ``!rq <username>``."""

from __future__ import annotations

from bot.core.event_router import CommandContext
from bot.database.models import ChatMessage
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.database.repositories.user import UserRepository
from bot.modules.base import Module, ModuleContext
from bot.modules.quotes.config import QuotesConfig
from bot.utils.time import as_utc

NOT_FOUND = "User not found."
NO_QUOTES = "No quotes found."
NO_USER_QUOTES = "No quotes found for that user."


async def _respond(ctx: CommandContext, text: str) -> None:
    """Reply in the thread of the command message."""
    responder = ctx.reply_threaded or ctx.reply
    await responder(text)


def format_quote(message: ChatMessage) -> str:
    """Format a logged message as ``[UTC date] @username: content``."""
    sent_at = as_utc(message.sent_at)
    date = sent_at.strftime("%Y-%m-%d %H:%M:%S") if sent_at else "unknown date"
    return f"[{date}] @{message.username}: {message.content}"


class QuotesModule(Module):
    name = "quotes"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = QuotesConfig.model_validate(context.config)
        self._messages: ChatMessageRepository | None = None
        self._users: UserRepository | None = None
        self._excluded_user_ids: frozenset[str] = frozenset()

    async def setup(self) -> None:
        database = self.context.services.database
        self._messages = ChatMessageRepository(database)
        self._users = UserRepository(database)
        self.subscribe(self.context.router.on_command("rq", self.on_rq))
        await self._resolve_excluded_ids()

    async def _resolve_excluded_ids(self) -> None:
        """Resolve configured exclusions to stable IDs where possible."""
        resolved: set[str] = set()
        for entry in self.config.excluded_users:
            if entry.isdigit():
                resolved.add(entry)
                continue
            try:
                user = await self._users.get_by_login(entry) if self._users else None
            except Exception as exc:
                self.log.warning(
                    "Could not resolve excluded user %s (%s)", entry, type(exc).__name__
                )
                continue
            if user is not None:
                resolved.add(user.id)
        self._excluded_user_ids = frozenset(resolved)

    async def on_rq(self, ctx: CommandContext) -> None:
        if self._messages is None or self._users is None or not ctx.channel_id:
            await _respond(ctx, NO_QUOTES)
            return

        try:
            message = await self._find_quote(ctx)
        except Exception as exc:
            self.log.warning("Quote lookup failed (%s)", type(exc).__name__)
            await _respond(ctx, NO_QUOTES)
            return

        if message is None:
            return
        # Use the username recorded with the message, not the user's current
        # login, so quotes from renamed users keep their original name.
        await _respond(ctx, format_quote(message))

    async def _find_quote(self, ctx: CommandContext) -> ChatMessage | None:
        assert self._messages is not None and self._users is not None

        if not ctx.args:
            message = await self._messages.get_random(
                ctx.channel_id, tuple(self._excluded_user_ids)
            )
            if message is None:
                await _respond(ctx, NO_QUOTES)
            return message

        requested = ctx.args[0].removeprefix("@").lower()
        if not requested or requested in self.config.excluded_users:
            await _respond(ctx, NOT_FOUND)
            return None

        user = await self._users.get_by_login(requested)
        if user is None or user.id in self._excluded_user_ids:
            await _respond(ctx, NOT_FOUND)
            return None

        message = await self._messages.get_random_by_user_id(ctx.channel_id, user.id)
        if message is None:
            await _respond(ctx, NO_USER_QUOTES)
        return message
