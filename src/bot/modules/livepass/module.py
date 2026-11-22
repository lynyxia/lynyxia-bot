"""Live pass list management: ``!livepass add|list|remove``.

Bot admins and the channel owner manage the per-channel list of users allowed
to run commands while the streamer is live. Names are resolved to stable
Twitch user IDs the same way as the AI chat allowlist.
"""

from __future__ import annotations

from bot.core.event_router import CommandContext
from bot.core.permissions import PermissionService
from bot.database.repositories.live_pass import LivePassRepository
from bot.database.repositories.user import UserRepository
from bot.modules.base import Module, ModuleContext
from bot.services.live_gate import LiveGate

USAGE = "Usage: !livepass add|list|remove [username|id]"


class LivePassModule(Module):
    name = "livepass"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._gate: LiveGate | None = None
        self._users: UserRepository | None = None

    async def setup(self) -> None:
        gate = self.context.services.live_gate
        if gate is None:
            gate = LiveGate(
                LivePassRepository(self.context.services.database),
                self.context.services.permissions or PermissionService(),
            )
        self._gate = gate
        if self._users is None:
            self._users = UserRepository(self.context.services.database)
        self.subscribe(self.context.router.on_command("livepass", self.on_livepass))

    async def on_livepass(self, ctx: CommandContext) -> None:
        permissions = self.context.services.permissions
        if permissions is not None and not permissions.can_manage_channel(ctx):
            # Stay quiet for unauthorized users; don't advertise the control.
            self.log.info(
                "Ignoring !livepass from unauthorized user %s in channel %s",
                ctx.author_login or ctx.author_id,
                ctx.channel_id,
            )
            return

        action = ctx.args[0].lower() if ctx.args else ""
        if action == "add":
            await self._add(ctx, ctx.args[1:])
        elif action == "remove":
            await self._remove(ctx, ctx.args[1:])
        elif action == "list":
            await self._list(ctx)
        else:
            await ctx.reply(USAGE)

    async def _resolve_user(self, name: str) -> tuple[str, str] | None:
        target = name.strip().lstrip("@")
        if not target:
            return None
        if target.isdigit():
            return target, target
        assert self._users is not None
        user = await self._users.get_by_login(target)
        if user is None:
            return None
        return user.id, user.login

    async def _add(self, ctx: CommandContext, args: list[str]) -> None:
        if len(args) != 1:
            await ctx.reply(USAGE)
            return
        resolved = await self._resolve_user(args[0])
        if resolved is None:
            await ctx.reply("User not found.")
            return
        user_id, login = resolved
        assert self._gate is not None
        await self._gate.add(ctx.channel_id, user_id, login, added_by=ctx.author_id)
        await ctx.reply(f"Added @{login} to the live pass list.")

    async def _remove(self, ctx: CommandContext, args: list[str]) -> None:
        if len(args) != 1:
            await ctx.reply(USAGE)
            return
        resolved = await self._resolve_user(args[0])
        if resolved is None:
            await ctx.reply("User not found.")
            return
        user_id, login = resolved
        assert self._gate is not None
        removed = await self._gate.remove(ctx.channel_id, user_id)
        await ctx.reply(
            f"Removed @{login} from the live pass list."
            if removed
            else f"@{login} was not on the live pass list."
        )

    async def _list(self, ctx: CommandContext) -> None:
        assert self._gate is not None
        entries = await self._gate.list_entries(ctx.channel_id)
        if not entries:
            await ctx.reply("The live pass list is empty.")
            return
        names = ", ".join(f"@{entry.login or entry.user_id}" for entry in entries)
        await ctx.reply(f"Live pass list: {names}")
