"""Per-channel gate that limits bot commands to authorized users while live.

When the streamer is offline everyone may use the bot as usual. When the
streamer is live, only users on the channel's live pass list may run commands —
the channel owner, bot admins and a small set of control commands are implicit
exceptions so nobody can be locked out of managing the bot.

The gate is installed on the event router, so it blocks the command handler
itself. Incoming messages are still dispatched to the ``"message"`` event and
logged; nothing here affects chat persistence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from bot.core.event_router import CommandContext
from bot.core.permissions import PermissionService
from bot.database.models import LivePass
from bot.database.repositories.live_pass import LivePassRepository
from bot.services.live_status import LiveStatusService

log = logging.getLogger(__name__)


class LiveGate:
    def __init__(
        self,
        repository: LivePassRepository,
        permissions: PermissionService | None,
        *,
        enabled: bool = True,
        exempt_commands: Iterable[str] = (),
        status: LiveStatusService | None = None,
    ) -> None:
        self._repository = repository
        self._permissions = permissions
        self.enabled = enabled
        self.exempt_commands = {name.lower().lstrip("!") for name in exempt_commands}
        self.status = status or LiveStatusService()
        self._passes: dict[str, set[str]] = {}
        self._loaded: set[str] = set()

    async def _pass_ids(self, channel_id: str) -> set[str]:
        if channel_id not in self._loaded:
            try:
                entries = await self._repository.list(channel_id)
                self._passes[channel_id] = {entry.user_id for entry in entries}
            except Exception:
                # A pass list that cannot be read is treated as empty: while
                # live, only the implicit exceptions remain allowed. Don't mark
                # it loaded so a recovered database is picked up next time.
                log.exception("Could not load live pass list for channel %s", channel_id)
                return set()
            self._loaded.add(channel_id)
        return self._passes[channel_id]

    async def contains(self, channel_id: str, user_id: str) -> bool:
        return user_id in await self._pass_ids(channel_id)

    async def add(
        self, channel_id: str, user_id: str, login: str, added_by: str = ""
    ) -> None:
        await self._repository.add(channel_id, user_id, login, added_by)
        await self._pass_ids(channel_id)
        self._passes.setdefault(channel_id, set()).add(user_id)

    async def remove(self, channel_id: str, user_id: str) -> bool:
        removed = await self._repository.remove(channel_id, user_id)
        await self._pass_ids(channel_id)
        self._passes.setdefault(channel_id, set()).discard(user_id)
        return removed

    async def list_entries(self, channel_id: str) -> list[LivePass]:
        return list(await self._repository.list(channel_id))

    async def is_allowed(self, ctx: CommandContext) -> bool:
        """Return whether ``ctx`` may run its command right now."""
        if not self.enabled:
            return True
        channel_id = ctx.channel_id
        if not self.status.is_live(channel_id):
            return True

        command = ctx.command.lower().lstrip("!")
        if command in self.exempt_commands:
            return True
        if self._permissions is not None and self._permissions.can_manage_channel(ctx):
            return True

        allowed = await self.contains(channel_id, ctx.author_id)
        if not allowed:
            log.info(
                "Live gate blocked !%s from user %s in channel %s",
                command,
                ctx.author_id,
                channel_id,
            )
        return allowed
