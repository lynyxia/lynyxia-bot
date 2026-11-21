"""Twitch client integration.

The core bot is the only place coupled to TwitchIO. It converts incoming
Twitch events into calls on the framework's ``EventRouter`` so modules stay
independent of TwitchIO's API.
"""

from __future__ import annotations

from typing import Any

from twitchio.ext import commands

from bot.core.event_router import CommandContext, EventRouter


class BotClient(commands.Bot):
    def __init__(self, *, router: EventRouter, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.router = router

    async def event_ready(self) -> None:
        await self.router.dispatch_event("ready", self.nick)

    async def event_message(self, message: Any) -> None:
        if getattr(message, "echo", False):
            return

        await self.router.dispatch_event("message", message)

        content = getattr(message, "content", "") or ""
        if not content.startswith("!"):
            return
        parts = content[1:].split()
        if not parts:
            return

        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)

        async def reply(text: str) -> None:
            if channel is not None and hasattr(channel, "send"):
                await channel.send(text)

        ctx = CommandContext(
            command=parts[0].lower(),
            args=parts[1:],
            author_id=str(getattr(author, "id", "") or ""),
            author_name=str(getattr(author, "name", "") or ""),
            channel_id=str(getattr(channel, "id", "") or ""),
            channel_name=str(getattr(channel, "name", "") or ""),
            content=content,
            reply=reply,
            raw=message,
        )
        await self.router.dispatch_command(ctx)
