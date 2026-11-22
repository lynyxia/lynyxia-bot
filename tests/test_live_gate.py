"""Tests for live-mode command gating and the !livepass module."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from bot.core.event_router import CommandContext, EventRouter
from bot.core.permissions import PermissionService
from bot.modules.livepass.module import LivePassModule
from bot.services import Services
from bot.services.live_gate import LiveGate
from bot.services.live_status import LiveStatusService


class FakePassRepo:
    def __init__(self, entries: list[tuple[str, str, str]] | None = None) -> None:
        self.rows: dict[tuple[str, str], SimpleNamespace] = {}
        self.list_calls = 0
        for channel_id, user_id, login in entries or []:
            self.rows[(channel_id, user_id)] = SimpleNamespace(
                channel_id=channel_id, user_id=user_id, login=login
            )

    async def add(
        self, channel_id: str, user_id: str, login: str, added_by: str = ""
    ) -> None:
        self.rows[(channel_id, user_id)] = SimpleNamespace(
            channel_id=channel_id, user_id=user_id, login=login, added_by=added_by
        )

    async def remove(self, channel_id: str, user_id: str) -> bool:
        return self.rows.pop((channel_id, user_id), None) is not None

    async def list(self, channel_id: str) -> list[SimpleNamespace]:
        self.list_calls += 1
        return [
            value for (channel, _user), value in self.rows.items() if channel == channel_id
        ]

    async def contains(self, channel_id: str, user_id: str) -> bool:
        return (channel_id, user_id) in self.rows


class BrokenPassRepo(FakePassRepo):
    async def list(self, channel_id: str) -> list[SimpleNamespace]:
        raise RuntimeError("database down")


class FakeUsers:
    async def get_by_login(self, login: str) -> object | None:
        if login.lower() == "known":
            return SimpleNamespace(id="123", login="known")
        return None


class FakePermissions:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def can_manage_channel(self, _ctx: CommandContext) -> bool:
        return self.allowed


def make_ctx(
    command: str = "chat",
    args: list[str] | None = None,
    *,
    author_id: str = "99",
    channel_id: str = "7",
) -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command=command,
        args=args or [],
        author_id=author_id,
        author_name="chatter",
        author_login="chatter",
        channel_id=channel_id,
        channel_name="channel",
        content=f"!{command}",
        reply=reply,
    )
    return ctx, replies


def make_gate(
    repo: FakePassRepo | None = None,
    permissions: PermissionService | FakePermissions | None = None,
    *,
    live: bool = True,
    enabled: bool = True,
    exempt: tuple[str, ...] = (),
) -> LiveGate:
    status = LiveStatusService()
    status.set_live("7", live)
    return LiveGate(
        repo or FakePassRepo(),
        permissions if permissions is not None else FakePermissions(False),
        enabled=enabled,
        exempt_commands=exempt,
        status=status,
    )


# ---------------------------------------------------------------------------
# Live status
# ---------------------------------------------------------------------------


def test_live_status_defaults_to_offline() -> None:
    status = LiveStatusService()
    assert status.is_live("7") is False
    status.set_live("7", True)
    assert status.is_live("7") is True
    status.set_live("7", False)
    assert status.is_live("7") is False


class FakeStreamHttp:
    def __init__(self, live_ids: list[str], *, fail: bool = False) -> None:
        self.live_ids = live_ids
        self.fail = fail

    def get_streams(self, *, user_ids: list[str]):
        return self._iterate()

    async def _iterate(self):
        if self.fail:
            raise RuntimeError("helix down")
        for user_id in self.live_ids:
            yield SimpleNamespace(user=SimpleNamespace(id=user_id))


async def test_live_status_hydrates_from_helix() -> None:
    status = LiveStatusService()
    await status.hydrate(FakeStreamHttp(["7"]), ["7", "8"])
    assert status.is_live("7") is True
    assert status.is_live("8") is False


async def test_live_status_hydration_fails_open() -> None:
    status = LiveStatusService()
    await status.hydrate(FakeStreamHttp([], fail=True), ["7"])
    assert status.is_live("7") is False


# ---------------------------------------------------------------------------
# Command gate
# ---------------------------------------------------------------------------


async def test_offline_channel_allows_everyone() -> None:
    repo = FakePassRepo()
    gate = make_gate(repo, live=False)
    ctx, _ = make_ctx()

    assert await gate.is_allowed(ctx) is True
    assert repo.list_calls == 0


async def test_live_channel_blocks_unlisted_user() -> None:
    gate = make_gate()
    ctx, _ = make_ctx()

    assert await gate.is_allowed(ctx) is False


async def test_live_channel_allows_listed_user() -> None:
    gate = make_gate(FakePassRepo([("7", "99", "chatter")]))
    ctx, _ = make_ctx()

    assert await gate.is_allowed(ctx) is True


async def test_live_channel_allows_channel_owner_and_admin() -> None:
    owner_gate = make_gate(permissions=PermissionService([]))
    owner_ctx, _ = make_ctx(author_id="7", channel_id="7")
    assert await owner_gate.is_allowed(owner_ctx) is True

    admin_gate = make_gate(permissions=PermissionService(["99"]))
    admin_ctx, _ = make_ctx()
    assert await admin_gate.is_allowed(admin_ctx) is True


async def test_exempt_commands_work_while_live() -> None:
    gate = make_gate(exempt=("livepass",))
    ctx, _ = make_ctx(command="livepass")
    assert await gate.is_allowed(ctx) is True


async def test_disabled_gate_allows_everyone() -> None:
    gate = make_gate(enabled=False)
    ctx, _ = make_ctx()
    assert await gate.is_allowed(ctx) is True


async def test_unreadable_pass_list_blocks_unlisted_users() -> None:
    gate = make_gate(BrokenPassRepo())
    ctx, _ = make_ctx()
    assert await gate.is_allowed(ctx) is False


async def test_router_fails_open_when_gate_raises() -> None:
    async def broken_gate(_ctx: CommandContext) -> bool:
        raise RuntimeError("gate down")

    router = EventRouter(command_gate=broken_gate)
    called: list[str] = []

    async def handler(_ctx: CommandContext) -> None:
        called.append("called")

    router.on_command("ping", handler)
    ctx, _ = make_ctx(command="ping")
    await router.dispatch_command(ctx)
    assert called == ["called"]


# ---------------------------------------------------------------------------
# Gate list mutations
# ---------------------------------------------------------------------------


async def test_gate_add_and_remove_update_cached_list() -> None:
    repo = FakePassRepo()
    gate = make_gate(repo)

    await gate.add("7", "99", "chatter", "owner")
    assert await gate.contains("7", "99") is True
    assert repo.list_calls == 1  # loaded once, then served from cache

    assert await gate.remove("7", "99") is True
    assert await gate.contains("7", "99") is False
    assert repo.list_calls == 1


async def test_gate_list_entries_returns_persisted_rows() -> None:
    repo = FakePassRepo([("7", "99", "chatter")])
    gate = make_gate(repo)
    entries = await gate.list_entries("7")
    assert [(entry.user_id, entry.login) for entry in entries] == [("99", "chatter")]


# ---------------------------------------------------------------------------
# !livepass module
# ---------------------------------------------------------------------------


class FakeGate:
    def __init__(self, entries: list[tuple[str, str, str]] | None = None) -> None:
        self.repo = FakePassRepo(entries)
        self.add_calls: list[tuple[str, str, str, str]] = []
        self.remove_calls: list[tuple[str, str]] = []

    async def add(
        self, channel_id: str, user_id: str, login: str, added_by: str = ""
    ) -> None:
        self.add_calls.append((channel_id, user_id, login, added_by))
        await self.repo.add(channel_id, user_id, login, added_by)

    async def remove(self, channel_id: str, user_id: str) -> bool:
        self.remove_calls.append((channel_id, user_id))
        return await self.repo.remove(channel_id, user_id)

    async def list_entries(self, channel_id: str):
        return await self.repo.list(channel_id)


def make_module(settings, *, gate=None, permissions=None):
    gate = gate or FakeGate()
    context = SimpleNamespace(
        settings=settings,
        config={},
        router=EventRouter(),
        services=Services(
            database=object(),
            permissions=permissions or FakePermissions(),
            live_gate=gate,
        ),
        log=logging.getLogger("test.livepass"),
    )
    module = LivePassModule(context)
    module._users = FakeUsers()
    return module, gate


async def test_livepass_adds_numeric_id(settings) -> None:
    module, gate = make_module(settings)
    ctx, replies = make_ctx("livepass", ["add", "123"])
    await module.setup()

    await module.on_livepass(ctx)

    assert gate.add_calls == [("7", "123", "123", "99")]
    assert replies == ["Added @123 to the live pass list."]


async def test_livepass_adds_resolved_login(settings) -> None:
    module, gate = make_module(settings)
    ctx, replies = make_ctx("livepass", ["add", "@known"])
    await module.setup()

    await module.on_livepass(ctx)

    assert gate.add_calls == [("7", "123", "known", "99")]
    assert replies == ["Added @known to the live pass list."]


async def test_livepass_reports_unknown_user(settings) -> None:
    module, gate = make_module(settings)
    ctx, replies = make_ctx("livepass", ["add", "missing"])
    await module.setup()

    await module.on_livepass(ctx)

    assert gate.add_calls == []
    assert replies == ["User not found."]


async def test_livepass_lists_entries(settings) -> None:
    module, _gate = make_module(settings, gate=FakeGate([("7", "123", "known")]))
    ctx, replies = make_ctx("livepass", ["list"])
    await module.setup()

    await module.on_livepass(ctx)

    assert replies == ["Live pass list: @known"]


async def test_livepass_reports_empty_list(settings) -> None:
    module, _gate = make_module(settings)
    ctx, replies = make_ctx("livepass", ["list"])
    await module.setup()

    await module.on_livepass(ctx)

    assert replies == ["The live pass list is empty."]


async def test_livepass_removes_entry(settings) -> None:
    module, gate = make_module(settings, gate=FakeGate([("7", "123", "known")]))
    ctx, replies = make_ctx("livepass", ["remove", "123"])
    await module.setup()

    await module.on_livepass(ctx)

    assert gate.remove_calls == [("7", "123")]
    assert replies == ["Removed @123 from the live pass list."]


@pytest.mark.parametrize("args", [[], ["add"], ["banana"]])
async def test_livepass_usage_for_bad_arguments(settings, args) -> None:
    module, gate = make_module(settings)
    ctx, replies = make_ctx("livepass", args)
    await module.setup()

    await module.on_livepass(ctx)

    assert gate.add_calls == []
    assert gate.remove_calls == []
    assert replies == ["Usage: !livepass add|list|remove [username|id]"]


async def test_livepass_is_silent_for_unauthorized_users(settings) -> None:
    module, gate = make_module(settings, permissions=FakePermissions(False))
    ctx, replies = make_ctx("livepass", ["add", "123"])
    await module.setup()

    await module.on_livepass(ctx)

    assert gate.add_calls == []
    assert replies == []
