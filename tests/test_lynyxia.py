"""Tests for the per-channel output toggle."""

from __future__ import annotations

import logging

from bot.core.event_router import CommandContext, EventRouter
from bot.modules.lynyxia.module import LynxiaModule
from bot.services import Services
from bot.services.output_gate import ChannelOutputGate


class FakeGate:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.set_calls: list[tuple[str, bool]] = []

    async def is_enabled(self, channel_id: str) -> bool:
        return self.enabled

    async def set_enabled(self, channel_id: str, enabled: bool) -> None:
        self.set_calls.append((channel_id, enabled))
        self.enabled = enabled


class FakePermissions:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def can_manage_channel(self, _ctx: CommandContext) -> bool:
        return self.allowed


def make_module(settings, *, gate=None, permissions=None):
    gate = gate or FakeGate()
    permissions = permissions or FakePermissions()
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": {},
            "router": EventRouter(),
            "services": Services(output_gate=gate, permissions=permissions),
            "log": logging.getLogger("test.lynyxia"),
        },
    )()
    return LynxiaModule(context), gate


def make_ctx(args: list[str]) -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(f"reply:{text}")

    async def reply_force(text: str) -> None:
        replies.append(f"force:{text}")

    ctx = CommandContext(
        command="lynyxia",
        args=args,
        author_id="99",
        author_name="chatter",
        author_login="chatter",
        channel_id="7",
        channel_name="channel",
        content="!lynyxia " + " ".join(args),
        reply=reply,
        reply_force=reply_force,
    )
    return ctx, replies


async def test_disable_uses_forced_reply_and_updates_gate(settings) -> None:
    module, gate = make_module(settings)
    ctx, replies = make_ctx(["disable"])

    await module.setup()
    await module.on_lynyxia(ctx)

    assert gate.set_calls == [("7", False)]
    assert replies == ["force:Chat output disabled in this channel."]


async def test_enable_uses_forced_reply_and_updates_gate(settings) -> None:
    module, gate = make_module(settings, gate=FakeGate(enabled=False))
    ctx, replies = make_ctx(["enable"])

    await module.setup()
    await module.on_lynyxia(ctx)

    assert gate.set_calls == [("7", True)]
    assert replies == ["force:Chat output enabled in this channel."]


async def test_bare_command_reports_status(settings) -> None:
    module, _gate = make_module(settings, gate=FakeGate(enabled=False))
    ctx, replies = make_ctx([])

    await module.setup()
    await module.on_lynyxia(ctx)

    assert replies == [
        "force:Chat output is disabled in this channel. Usage: !lynyxia disable|enable"
    ]


async def test_unknown_action_shows_usage(settings) -> None:
    module, gate = make_module(settings)
    ctx, replies = make_ctx(["banana"])

    await module.setup()
    await module.on_lynyxia(ctx)

    assert gate.set_calls == []
    assert replies == ["force:Usage: !lynyxia disable|enable"]


async def test_unauthorized_user_is_silent(settings) -> None:
    module, gate = make_module(settings, permissions=FakePermissions(allowed=False))
    ctx, replies = make_ctx(["disable"])

    await module.setup()
    await module.on_lynyxia(ctx)

    assert gate.set_calls == []
    assert replies == []


class FakeRepo:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.reads = 0
        self.writes: list[tuple[str, bool]] = []

    async def is_enabled(self, channel_id: str) -> bool:
        self.reads += 1
        return self.enabled

    async def set_output_enabled(self, channel_id: str, enabled: bool) -> None:
        self.writes.append((channel_id, enabled))
        self.enabled = enabled


async def test_output_gate_caches_reads_and_updates_cache_on_write() -> None:
    repo = FakeRepo()
    gate = ChannelOutputGate(repo)

    assert await gate.is_enabled("7") is True
    assert await gate.is_enabled("7") is True
    assert repo.reads == 1

    await gate.set_enabled("7", False)
    assert repo.writes == [("7", False)]
    assert await gate.is_enabled("7") is False
    assert repo.reads == 1


class BrokenRepo:
    async def is_enabled(self, channel_id: str) -> bool:
        raise RuntimeError("database down")

    async def set_output_enabled(self, channel_id: str, enabled: bool) -> None:
        raise AssertionError("should not be called")


async def test_output_gate_fails_open_on_database_error() -> None:
    gate = ChannelOutputGate(BrokenRepo())
    assert await gate.is_enabled("7") is True
