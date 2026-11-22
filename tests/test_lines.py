"""Tests for the !lines module."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from bot.core.event_router import CommandContext, EventRouter
from bot.core.module_manager import ModuleManager
from bot.modules.lines import LinesModule
from bot.modules.lines.config import LinesConfig
from bot.services import Services


class FakeUser:
    def __init__(self, user_id: str = "42", login: str = "chatter") -> None:
        self.id = user_id
        self.login = login


class FakeUsers:
    def __init__(self) -> None:
        chatter = FakeUser()
        self.by_login: dict[str, FakeUser] = {"chatter": chatter, "12345": FakeUser("99", "12345")}
        self.by_id: dict[str, FakeUser] = {"42": chatter}
        self.login_calls: list[str] = []
        self.id_calls: list[str] = []

    async def get_by_login(self, login: str) -> FakeUser | None:
        self.login_calls.append(login)
        return self.by_login.get(login.lower())

    async def get_by_id(self, user_id: str) -> FakeUser | None:
        self.id_calls.append(user_id)
        return self.by_id.get(user_id)


class FakeMessages:
    def __init__(
        self,
        count: int = 0,
        first: datetime | None = None,
        error: Exception | None = None,
    ) -> None:
        self.count = count
        self.first = first
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def count_and_first(
        self, channel_id: str, user_id: str
    ) -> tuple[int, datetime | None]:
        self.calls.append((channel_id, user_id))
        if self.error is not None:
            raise self.error
        return self.count, self.first


def make_ctx(args: list[str]) -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command="lines",
        args=args,
        author_id="99",
        author_name="chatter",
        channel_id="7",
        channel_name="channel",
        content="!lines " + " ".join(args),
        reply=reply,
    )
    return ctx, replies


def make_module(settings, monkeypatch, *, count=0, first=None, error=None, config=None):
    users = FakeUsers()
    messages = FakeMessages(count, first, error)
    monkeypatch.setattr("bot.modules.lines.module.UserRepository", lambda _db: users)
    monkeypatch.setattr("bot.modules.lines.module.ChatMessageRepository", lambda _db: messages)
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": config or {},
            "router": EventRouter(),
            "services": Services(),
            "log": logging.getLogger("test.lines"),
        },
    )()
    return LinesModule(context), users, messages


def test_config_defaults() -> None:
    assert LinesConfig.model_validate({}).date_format == "%Y-%m-%d %H:%M:%S"


async def test_lines_requires_username(settings, monkeypatch) -> None:
    module, users, messages = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx([])

    await module.on_lines(ctx)

    assert replies == ["Usage: !lines <username>"]
    assert users.login_calls == [] and users.id_calls == []
    assert messages.calls == []


async def test_lines_reports_count_and_first(settings, monkeypatch) -> None:
    first = datetime(2021, 8, 11, 17, 25, 54, tzinfo=UTC)
    module, _users, messages = make_module(settings, monkeypatch, count=1234, first=first)
    await module.setup()
    ctx, replies = make_ctx(["@chatter"])

    await module.on_lines(ctx)

    assert replies == ["chatter has sent 1,234 messages since 2021-08-11 17:25:54."]
    assert messages.calls == [("7", "42")]


async def test_lines_custom_date_format(settings, monkeypatch) -> None:
    first = datetime(2021, 8, 11, 17, 25, 54, tzinfo=UTC)
    module, _users, _messages = make_module(
        settings,
        monkeypatch,
        count=3,
        first=first,
        config={"date_format": "%Y/%m/%d %H:%M:%S"},
    )
    await module.setup()
    ctx, replies = make_ctx(["chatter"])

    await module.on_lines(ctx)

    assert replies == ["chatter has sent 3 messages since 2021/08/11 17:25:54."]


async def test_lines_unknown_user(settings, monkeypatch) -> None:
    module, _users, messages = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["nobody"])

    await module.on_lines(ctx)

    assert replies == ["No messages found for user 'nobody'."]
    assert messages.calls == []


async def test_lines_numeric_id_uses_id_lookup(settings, monkeypatch) -> None:
    module, users, messages = make_module(settings, monkeypatch, count=2)
    await module.setup()
    ctx, replies = make_ctx(["42"])

    await module.on_lines(ctx)

    assert users.id_calls == ["42"]
    assert users.login_calls == []
    assert messages.calls == [("7", "42")]
    assert replies == ["chatter has sent 2 messages since unknown."]


async def test_lines_numeric_falls_back_to_login(settings, monkeypatch) -> None:
    module, users, messages = make_module(settings, monkeypatch, count=4)
    await module.setup()
    ctx, _replies = make_ctx(["12345"])

    await module.on_lines(ctx)

    assert users.id_calls == ["12345"]
    assert users.login_calls == ["12345"]
    assert messages.calls == [("7", "99")]


async def test_lines_no_messages_for_known_user(settings, monkeypatch) -> None:
    module, _users, _messages = make_module(settings, monkeypatch, count=0)
    await module.setup()
    ctx, replies = make_ctx(["chatter"])

    await module.on_lines(ctx)

    assert replies == ["No messages found for user 'chatter'."]


async def test_lines_reports_lookup_errors(settings, monkeypatch) -> None:
    module, _users, _messages = make_module(
        settings, monkeypatch, error=RuntimeError("db down")
    )
    await module.setup()
    ctx, replies = make_ctx(["chatter"])

    await module.on_lines(ctx)

    assert replies == ["Error searching logs."]


def test_discovery_finds_lines(settings) -> None:
    manager = ModuleManager(settings, EventRouter(), Services())
    assert "lines" in manager.discover()
