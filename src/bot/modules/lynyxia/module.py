"""Per-channel output control: ``!lynyxia disable`` / ``!lynyxia enable``.

Bot admins and the channel owner can silence the bot in a single channel. The
bot stays connected and keeps logging messages; it simply stops sending chat
output. Only this command's own confirmation is allowed through the output
gate, so an admin can always turn output back on.
"""

from __future__ import annotations

from bot.core.event_router import CommandContext
from bot.database.repositories.channel_state import ChannelStateRepository
from bot.modules.base import Module, ModuleContext
from bot.services.output_gate import ChannelOutputGate

USAGE = "Usage: !lynyxia disable|enable"
DISABLED = "Chat output disabled in this channel."
ENABLED = "Chat output enabled in this channel."


class LynxiaModule(Module):
    name = "lynyxia"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._gate: ChannelOutputGate | None = None

    async def setup(self) -> None:
        gate = self.context.services.output_gate
        if gate is None:
            # Keeps the module usable when constructed with a minimal context
            # (e.g. unit tests) where the composition root did not inject one.
            gate = ChannelOutputGate(
                ChannelStateRepository(self.context.services.database)
            )
        self._gate = gate
        self.subscribe(self.context.router.on_command("lynyxia", self.on_lynyxia))

    async def on_lynyxia(self, ctx: CommandContext) -> None:
        permissions = self.context.services.permissions
        if permissions is not None and not permissions.can_manage_channel(ctx):
            # Stay quiet for unauthorized users; don't advertise the control.
            self.log.info(
                "Ignoring !lynyxia from unauthorized user %s in channel %s",
                ctx.author_login or ctx.author_id,
                ctx.channel_id,
            )
            return

        assert self._gate is not None
        action = ctx.args[0].lower() if ctx.args else "status"

        if action == "disable":
            await self._gate.set_enabled(ctx.channel_id, False)
            await self._reply(ctx, DISABLED)
        elif action == "enable":
            await self._gate.set_enabled(ctx.channel_id, True)
            await self._reply(ctx, ENABLED)
        elif action in ("status", ""):
            state = "enabled" if await self._gate.is_enabled(ctx.channel_id) else "disabled"
            await self._reply(ctx, f"Chat output is {state} in this channel. {USAGE}")
        else:
            await self._reply(ctx, USAGE)

    @staticmethod
    async def _reply(ctx: CommandContext, text: str) -> None:
        # These confirmations must be visible even while output is disabled.
        responder = ctx.reply_force or ctx.reply
        await responder(text)
