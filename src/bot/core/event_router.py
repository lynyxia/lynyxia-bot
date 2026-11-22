"""Central event/command router.

Modules register handlers here instead of depending on TwitchIO directly.
Handlers are async callables; failures are isolated so one bad module cannot
break event delivery for the others.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[None]]


@dataclass
class CommandContext:
    """Everything a command handler needs to act on an incoming command."""

    command: str
    args: list[str]
    author_id: str
    author_name: str
    channel_id: str
    channel_name: str
    content: str
    reply: Callable[[str], Awaitable[None]]
    author_login: str | None = None
    raw: Any = field(default=None, repr=False)
    reply_threaded: Callable[[str], Awaitable[None]] | None = None
    thread_id: str | None = None

    def __post_init__(self) -> None:
        # Handlers that don't need threading can use the plain reply.
        if self.reply_threaded is None:
            self.reply_threaded = self.reply


@dataclass(frozen=True)
class ChatMessageEvent:
    """Normalized incoming chat message delivered to ``"message"`` handlers.

    Modules consume this instead of TwitchIO objects, keeping them decoupled
    from the chat library.
    """

    author_id: str
    author_login: str
    author_name: str
    channel_id: str
    channel_login: str
    channel_name: str
    content: str
    message_id: str | None
    sent_at: datetime | None
    raw: Any = field(default=None, repr=False)


@dataclass
class Subscription:
    """Handle that undoes a registration when ``unsubscribe()`` is called."""

    _unregister: Callable[[], None]

    def unsubscribe(self) -> None:
        self._unregister()


class EventRouter:
    def __init__(self) -> None:
        self._events: dict[str, list[Handler]] = defaultdict(list)
        self._commands: dict[str, list[Handler]] = defaultdict(list)

    def on_event(self, event: str, handler: Handler) -> Subscription:
        """Register ``handler`` for a named event (e.g. ``"message"``)."""
        key = event.lower()
        return self._subscribe(self._events, key, handler)

    def on_command(self, command: str, handler: Handler) -> Subscription:
        """Register ``handler`` for a command name (with or without ``!``)."""
        key = command.lower().lstrip("!")
        if not key:
            raise ValueError("command name must not be empty")
        return self._subscribe(self._commands, key, handler)

    @staticmethod
    def _subscribe(
        registry: dict[str, list[Handler]], key: str, handler: Handler
    ) -> Subscription:
        registry[key].append(handler)
        return Subscription(lambda: EventRouter._remove(registry, key, handler))

    @staticmethod
    def _remove(registry: dict[str, list[Handler]], key: str, handler: Handler) -> None:
        handlers = registry.get(key)
        if handlers and handler in handlers:
            handlers.remove(handler)
            if not handlers:
                registry.pop(key, None)

    async def dispatch_event(self, event: str, *args: Any, **kwargs: Any) -> None:
        for handler in list(self._events.get(event.lower(), ())):
            try:
                await handler(*args, **kwargs)
            except Exception:
                log.exception("Event handler failed for event=%s", event)

    async def dispatch_command(self, ctx: CommandContext) -> None:
        for handler in list(self._commands.get(ctx.command.lower(), ())):
            try:
                await handler(ctx)
            except Exception:
                log.exception("Command handler failed for !%s", ctx.command)
