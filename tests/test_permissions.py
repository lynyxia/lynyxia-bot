from bot.core.event_router import CommandContext
from bot.core.permissions import PermissionService


def make_context(user_id: str) -> CommandContext:
    async def reply(_text: str) -> None:
        return None

    return CommandContext(
        command="chatsettings",
        args=[],
        author_id=user_id,
        author_name="u",
        channel_id="1",
        channel_name="c",
        content="!chatsettings",
        reply=reply,
    )


def test_admin_check() -> None:
    permissions = PermissionService(["42"])
    assert permissions.is_admin("42")
    assert not permissions.is_admin("7")
    assert permissions.is_admin_context(make_context("42"))
    assert not permissions.is_admin_context(make_context("7"))
