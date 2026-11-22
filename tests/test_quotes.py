"""Unit tests for the ``!rq`` quotes module."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bot.core.event_router import CommandContext, EventRouter
from bot.core.module_manager import ModuleManager
from bot.modules.base import ModuleContext
from bot.modules.quotes import QuotesModule
from bot.modules.quotes.config import QuotesConfig
from bot.services import Services


class FakeUser:
    def __init__(self, user_id: str, login: str) -> None:
        self.id = user_id
        self.login = login


class FakeMessage:
    def __init__(
        self, username: str, content: str, sent_at: datetime | None = None
    ) -> None:
        self.username = username
        self.content = content
        self.sent_at = sent_at or datetime(2021, 8, 11, 17, 25, 54, tzinfo=UTC)


class FakeMessages:
    def __init__(self) -> None:
        self.random_result: FakeMessage | None = None
        self.user_result: FakeMessage | None = None
        self.calls: list[tuple] = []

    async def get_random(
        self, channel_id: str, excluded_user_ids: tuple[str, ...] = ()
    ) -> FakeMessage | None:
        self.calls.append(("random", channel_id, excluded_user_ids))
        return self.random_result

    async def get_random_by_user_id(
        self, channel_id: str, user_id: str
    ) -> FakeMessage | None:
        self.calls.append(("user", channel_id, user_id))
        return self.user_result


class FakeUsers:
    def __init__(self, users: dict[str, FakeUser] | None = None) -> None:
        self.users = users or {}
        self.calls: list[str] = []

    async def get_by_login(self, login: str) -> FakeUser | None:
        self.calls.append(login)
        return self.users.get(login.lower())


def make_module(
    settings,
    monkeypatch,
    config: dict | None = None,
    messages: FakeMessages | None = None,
    users: FakeUsers | None = None,
) -> tuple[QuotesModule, FakeMessages, FakeUsers]:
    messages = messages or FakeMessages()
    users = users or FakeUsers()
    monkeypatch.setattr(
        "bot.modules.quotes.module.ChatMessageRepository", lambda _db: messages
    )
    monkeypatch.setattr(
        "bot.modules.quotes.module.UserRepository", lambda _db: users
    )
    context = ModuleContext(
        settings=settings,
        config=config or {},
        router=EventRouter(),
        services=Services(database=object()),
        log=logging.getLogger("test.quotes"),
    )
    return QuotesModule(context), messages, users


def make_ctx(args: list[str] | None = None) -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)
        ctx.standalone_used = True  # type: ignore[attr-defined]

    async def reply_threaded(text: str) -> None:
        replies.append(text)
        ctx.threaded_used = True  # type: ignore[attr-defined]

    ctx = CommandContext(
        command="rq",
        args=args or [],
        author_id="1",
        author_name="chatter",
        channel_id="7",
        channel_name="channel",
        content="!rq",
        reply=reply,
        reply_threaded=reply_threaded,
    )
    return ctx, replies


def test_config_normalizes_excluded_users() -> None:
    config = QuotesConfig(excluded_users=["@SomeUser", "someuser", "", "42"])
    assert config.excluded_users == ["42", "someuser"]

    with pytest.raises(ValidationError):
        QuotesConfig(excluded_users=[1])


async def test_setup_registers_rq_command(
    settings, monkeypatch
) -> None:
    module, _messages, _users = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx()

    await module.context.router.dispatch_command(ctx)

    assert replies == ["No quotes found."]


async def test_random_quote_uses_message_username(
    settings, monkeypatch
) -> None:
    messages = FakeMessages()
    messages.random_result = FakeMessage("oldname", "a funny message")
    module, messages, _users = make_module(settings, monkeypatch, messages=messages)
    await module.setup()
    ctx, replies = make_ctx()

    await module.on_rq(ctx)

    assert replies == ["[2021-08-11 17:25:54] @oldname: a funny message"]
    assert getattr(ctx, "threaded_used", False)
    assert not getattr(ctx, "standalone_used", False)
    assert messages.calls[0] == ("random", "7", ())


async def test_targeted_quote_resolves_user_id_and_old_username(
    settings, monkeypatch
) -> None:
    users = FakeUsers({"chatter": FakeUser("42", "chatter")})
    messages = FakeMessages()
    messages.user_result = FakeMessage("oldname", "historic message")
    module, messages, users = make_module(
        settings, monkeypatch, messages=messages, users=users
    )
    await module.setup()
    ctx, replies = make_ctx(["@Chatter"])

    await module.on_rq(ctx)

    assert users.calls == ["chatter"]
    assert messages.calls == [("user", "7", "42")]
    assert replies == ["[2021-08-11 17:25:54] @oldname: historic message"]


async def test_excluded_username_returns_not_found(
    settings, monkeypatch
) -> None:
    users = FakeUsers({"blocked": FakeUser("42", "blocked")})
    module, _messages, users = make_module(
        settings,
        monkeypatch,
        config={"excluded_users": ["Blocked"]},
        users=users,
    )
    await module.setup()
    users.calls.clear()  # setup resolves exclusions; the request must not
    ctx, replies = make_ctx(["blocked"])

    await module.on_rq(ctx)

    assert replies == ["User not found."]
    assert users.calls == []


async def test_excluded_user_id_returns_not_found(
    settings, monkeypatch
) -> None:
    users = FakeUsers({"someone": FakeUser("42", "someone")})
    module, _messages, _users = make_module(
        settings,
        monkeypatch,
        config={"excluded_users": ["42"]},
        users=users,
    )
    await module.setup()
    ctx, replies = make_ctx(["someone"])

    await module.on_rq(ctx)

    assert replies == ["User not found."]


async def test_unknown_user_returns_not_found(settings, monkeypatch) -> None:
    module, _messages, _users = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["nobody"])

    await module.on_rq(ctx)

    assert replies == ["User not found."]


async def test_user_without_messages_has_specific_reply(
    settings, monkeypatch
) -> None:
    users = FakeUsers({"someone": FakeUser("42", "someone")})
    module, _messages, _users = make_module(
        settings, monkeypatch, users=users
    )
    await module.setup()
    ctx, replies = make_ctx(["someone"])

    await module.on_rq(ctx)

    assert replies == ["No quotes found for that user."]


async def test_random_quotes_exclude_configured_user_ids(
    settings, monkeypatch
) -> None:
    users = FakeUsers({"blocked": FakeUser("42", "blocked")})
    messages = FakeMessages()
    module, messages, _users = make_module(
        settings,
        monkeypatch,
        config={"excluded_users": ["blocked"]},
        messages=messages,
        users=users,
    )
    await module.setup()
    ctx, _replies = make_ctx()

    await module.on_rq(ctx)

    assert messages.calls[0] == ("random", "7", ("42",))


def test_format_quote_converts_to_utc() -> None:
    from datetime import timedelta, timezone

    from bot.modules.quotes.module import format_quote

    message = FakeMessage(
        "Kappa123",
        "yeah, nice socks Kappa",
        sent_at=datetime(2021, 8, 11, 12, 25, 54, tzinfo=timezone(timedelta(hours=-5))),
    )
    assert format_quote(message) == (
        "[2021-08-11 17:25:54] @Kappa123: yeah, nice socks Kappa"
    )


async def test_error_replies_are_also_threaded(settings, monkeypatch) -> None:
    module, _messages, _users = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["nobody"])

    await module.on_rq(ctx)

    assert replies == ["User not found."]
    assert getattr(ctx, "threaded_used", False)
    assert not getattr(ctx, "standalone_used", False)


def test_discovery_finds_quotes_module(settings) -> None:
    manager = ModuleManager(settings, EventRouter(), Services())
    assert manager.discover()["quotes"] is QuotesModule
