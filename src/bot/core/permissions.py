"""Permission checks for administrative commands."""

from __future__ import annotations

from collections.abc import Iterable

from bot.core.event_router import CommandContext


class PermissionService:
    def __init__(self, admin_user_ids: Iterable[str] = ()) -> None:
        self._admin_ids = {str(user_id) for user_id in admin_user_ids}

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self._admin_ids

    def is_admin_context(self, ctx: CommandContext) -> bool:
        return self.is_admin(ctx.author_id)

    def is_channel_owner(self, ctx: CommandContext) -> bool:
        """The broadcaster's user ID is the channel ID in our events."""
        return bool(ctx.channel_id) and ctx.author_id == ctx.channel_id

    def can_manage_channel(self, ctx: CommandContext) -> bool:
        return self.is_admin_context(ctx) or self.is_channel_owner(ctx)
