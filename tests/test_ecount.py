"""Tests for the !ecount module."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from bot.core.event_router import CommandContext, EventRouter
from bot.core.module_manager import ModuleManager
from bot.modules.ecount import EcountModule
from bot.modules.ecount.config import EcountConfig
from bot.modules.ecount.module import build_patterns, parse_timeframe
from bot.services import Services


class FakeMessages:
    def __init__(self, total: int = 0, error: Exception | None = None) -> None:
        self.total = total
        self.error = error
        self.calls: list[tuple[str, str, str, datetime]] = []

    async def count_emote(
        self, channel_id: str, pattern: str, exclude_pattern: str, since: datetime
    ) -> int:
        self.calls.append((channel_id, pattern, exclude_pattern, since))
        if self.error is not None:
            raise self.error
        return self.total


def make_ctx(args: list[str], channel_id: str = "7") -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command="ecount",
        args=args,
        author_id="99",
        author_name="chatter",
        channel_id=channel_id,
        channel_name="channel",
        content="!ecount " + " ".join(args),
        reply=reply,
    )
    return ctx, replies


def make_module(settings, monkeypatch, *, total=0, error=None, config=None):
    messages = FakeMessages(total, error)
    monkeypatch.setattr(
        "bot.modules.ecount.module.ChatMessageRepository", lambda _db: messages
    )
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": config or {},
            "router": EventRouter(),
            "services": Services(),
            "log": logging.getLogger("test.ecount"),
        },
    )()
    return EcountModule(context), messages


def test_config_defaults_and_clamping() -> None:
    config = EcountConfig.model_validate({})
    assert config.default_days == 365
    assert config.max_days == 3650
    assert EcountConfig(default_days=9999, max_days=10).default_days == 10


def test_parse_timeframe() -> None:
    assert parse_timeframe("", default_days=365, max_days=3650) == 365
    assert parse_timeframe("nonsense", default_days=365, max_days=3650) == 365
    assert parse_timeframe("6m", default_days=365, max_days=3650) == 180
    assert parse_timeframe("2w", default_days=365, max_days=3650) == 14
    assert parse_timeframe("3d", default_days=365, max_days=3650) == 3
    assert parse_timeframe("1y", default_days=365, max_days=3650) == 365
    assert parse_timeframe("100y", default_days=365, max_days=10) == 10
    assert parse_timeframe("0d", default_days=365, max_days=3650) == 1


def test_build_patterns_are_case_sensitive_whole_words() -> None:
    pattern, exclude = build_patterns("Kappa")
    assert pattern == r"(?<![a-zA-Z0-9_.])Kappa(?![a-zA-Z0-9_.])"
    assert exclude == r"!ecount\s+Kappa"
    assert build_patterns("Pog.Champ")[0] == (
        r"(?<![a-zA-Z0-9_.])Pog\.Champ(?![a-zA-Z0-9_.])"
    )


async def test_ecount_replies_with_count_in_current_channel(settings, monkeypatch) -> None:
    module, messages = make_module(settings, monkeypatch, total=5)
    await module.setup()
    ctx, replies = make_ctx(["Kappa"])

    await module.on_ecount(ctx)

    assert replies == ["Kappa was used 5 times."]
    channel_id, pattern, exclude_pattern, since = messages.calls[0]
    assert channel_id == "7"
    assert pattern == r"(?<![a-zA-Z0-9_.])Kappa(?![a-zA-Z0-9_.])"
    assert exclude_pattern == r"!ecount\s+Kappa"
    elapsed_days = (datetime.now(UTC) - since).total_seconds() / 86_400
    assert 364 <= elapsed_days <= 366


async def test_ecount_uses_requested_timeframe_and_clamps(settings, monkeypatch) -> None:
    module, messages = make_module(
        settings, monkeypatch, config={"default_days": 365, "max_days": 10}
    )
    await module.setup()
    ctx, _replies = make_ctx(["Kappa", "100y"])

    await module.on_ecount(ctx)

    elapsed_days = (datetime.now(UTC) - messages.calls[0][3]).total_seconds() / 86_400
    assert 9 <= elapsed_days <= 11


async def test_ecount_singular_result(settings, monkeypatch) -> None:
    module, _messages = make_module(settings, monkeypatch, total=1)
    await module.setup()
    ctx, replies = make_ctx(["Kappa"])

    await module.on_ecount(ctx)

    assert replies == ["Kappa was used 1 time."]


async def test_ecount_no_results(settings, monkeypatch) -> None:
    module, _messages = make_module(settings, monkeypatch, total=0)
    await module.setup()
    ctx, replies = make_ctx(["Kappa"])

    await module.on_ecount(ctx)

    assert replies == ["No results found."]


async def test_ecount_validates_emote_name(settings, monkeypatch) -> None:
    module, messages = make_module(settings, monkeypatch, total=10)
    await module.setup()

    for emote in ("", "bad name", "a.*", "emoji😀"):
        ctx, replies = make_ctx([emote])
        await module.on_ecount(ctx)
        assert replies == ["Invalid emote name."]
    assert messages.calls == []


async def test_ecount_without_args_shows_usage(settings, monkeypatch) -> None:
    module, messages = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx([])

    await module.on_ecount(ctx)

    assert replies[0].startswith("Usage: !ecount")
    assert messages.calls == []


async def test_ecount_strips_leading_at(settings, monkeypatch) -> None:
    module, messages = make_module(settings, monkeypatch, total=2)
    await module.setup()
    ctx, replies = make_ctx(["@Kappa"])

    await module.on_ecount(ctx)

    assert replies == ["Kappa was used 2 times."]
    assert messages.calls[0][1] == r"(?<![a-zA-Z0-9_.])Kappa(?![a-zA-Z0-9_.])"


async def test_ecount_reports_lookup_errors(settings, monkeypatch) -> None:
    module, _messages = make_module(
        settings, monkeypatch, error=RuntimeError("db down")
    )
    await module.setup()
    ctx, replies = make_ctx(["Kappa"])

    await module.on_ecount(ctx)

    assert replies == ["Error searching logs."]


def test_discovery_finds_ecount(settings) -> None:
    manager = ModuleManager(settings, EventRouter(), Services())
    assert "ecount" in manager.discover()
