"""Tests for the admin-only 7TV unused-emotes report module."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from bot.core.event_router import CommandContext, EventRouter
from bot.core.permissions import PermissionService
from bot.modules.unused_emotes.config import UnusedEmotesConfig
from bot.modules.unused_emotes.module import UnusedEmotesModule
from bot.modules.unused_emotes.report import (
    build_patterns,
    parse_cutoff,
    parse_threshold,
    render_report,
)
from bot.modules.unused_emotes.seven_tv import SevenTVClient, SevenTVError
from bot.services import Services

EMOTES = [
    {"id": "abc", "name": "Kappa", "host": {
        "url": "//cdn.7tv.app/emote/abc",
        "files": [{"name": "2x.webp"}],
    }},
    {"id": "def", "name": "Pog.Champ", "data": {
        "host": {"url": "https://cdn.7tv.app/emote/def", "files": []},
    }},
    {"id": "ghi", "name": "Safe"},
]


def test_config_normalizes_and_validates() -> None:
    config = UnusedEmotesConfig(url_prefix="https://reports.example.com/")
    assert config.url_prefix == "https://reports.example.com"
    assert config.default_cutoff == "365d"
    assert config.default_threshold == 10
    assert config.reports_dir == "reports"

    with pytest.raises(ValidationError):
        UnusedEmotesConfig(url_prefix="reports.example.com")
    with pytest.raises(ValidationError):
        UnusedEmotesConfig(default_cutoff="forever")
    with pytest.raises(ValidationError):
        UnusedEmotesConfig(filename="../escape.html")


def test_parse_cutoff_uses_supported_units() -> None:
    now = datetime.now(UTC)
    for value, days in (("3d", 3), ("2w", 14), ("6m", 180), ("1y", 365)):
        cutoff = parse_cutoff(value)
        elapsed = (now - cutoff).total_seconds() / 86_400
        assert days - 1 <= elapsed <= days + 1
    for value in ("nonsense", "0d", "", "12h", "100d!"):
        with pytest.raises(ValueError):
            parse_cutoff(value)


def test_parse_threshold_is_strict() -> None:
    assert parse_threshold("", default=10, max_threshold=100) == 10
    assert parse_threshold(" 25 ", default=10, max_threshold=100) == 25
    for value in ("-1", "abc", "0", "101", "1.5"):
        with pytest.raises(ValueError):
            parse_threshold(value, default=10, max_threshold=100)


def test_build_patterns_escape_special_characters() -> None:
    pattern, exclude = build_patterns("Pog.Champ")
    assert pattern == r"(?<![a-zA-Z0-9_.])Pog\.Champ(?![a-zA-Z0-9_.])"
    assert exclude == r"!ecount\s+Pog\.Champ"


def test_emote_image_url_only_accepts_https() -> None:
    report = render_report(
        [({"id": "abc", "name": "Kappa", "host": {
            "url": "javascript:alert(1)", "files": [{"name": "2x.webp"}],
        }}, "Kappa", 2)],
        5,
        datetime.now(UTC),
        10,
        "channel",
    )
    assert "javascript:" not in report


def test_report_html_escapes_untrusted_values() -> None:
    malicious = {
        "id": 'x"onload="alert(1)',
        "name": '<script>alert("x")</script>',
        "host": {
            "url": "https://cdn.7tv.app/emote/x",
            "files": [{"name": "2x.webp"}],
        },
    }
    report = render_report(
        [(malicious, malicious["name"], 1), (EMOTES[1], "Pog.Champ", 0)],
        2,
        datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        10,
        "<channel>",
    )
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "&lt;channel&gt;" in report
    assert 'alt="&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"' in report
    assert "2026-01-02 03:04:00 UTC" in report
    assert "Qwen 3.6 35B" in report  # requested footer is retained
    assert "Kappa" not in report


def test_report_shows_success_when_nothing_is_under_threshold() -> None:
    report = render_report([], 3, datetime.now(UTC), 10, "testchannel")
    assert "All emotes meet the threshold!" in report
    assert "testchannel" in report
    assert "<table>" not in report


async def test_seven_tv_client_resolves_set_and_emotes(monkeypatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("bot.modules.unused_emotes.seven_tv.asyncio.sleep", no_sleep)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/users/twitch/7"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(429)
            return httpx.Response(200, json={"emote_set_id": "abc"})
        if request.url.path.endswith("/emote-sets/abc"):
            return httpx.Response(200, json={"emotes": EMOTES + ["not-a-dict"]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        api = SevenTVClient("https://7tv.io/v3/", 5, client=client)
        assert await api.emote_set_id("7") == "abc"
        assert attempts == 2  # 429 retried
        assert await api.active_emotes("abc") == EMOTES


async def test_seven_tv_client_wraps_failures(monkeypatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("bot.modules.unused_emotes.seven_tv.asyncio.sleep", no_sleep)
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        api = SevenTVClient("https://7tv.io/v3", 5, client=client)
        with pytest.raises(SevenTVError):
            await api.emote_set_id("7")


class FakeMessages:
    def __init__(self, totals: dict[str, int] | None = None) -> None:
        self.totals = totals or {}
        self.calls: list[tuple[str, str, str, datetime]] = []

    async def count_emote(
        self, channel_id: str, pattern: str, exclude_pattern: str, since: datetime
    ) -> int:
        self.calls.append((channel_id, pattern, exclude_pattern, since))
        return self.totals.get(pattern, 99)


class FakeSevenTV:
    def __init__(
        self,
        *,
        set_id: str | None = "abc",
        emotes: list[dict] | None = None,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.set_id = set_id
        self.emotes = EMOTES if emotes is None else emotes
        self.error = error
        self.gate = gate

    async def emote_set_id(self, twitch_id: str) -> str | None:
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.set_id

    async def active_emotes(self, emote_set_id: str) -> list[dict]:
        return self.emotes


def make_ctx(
    args: list[str] | None = None, *, author_id: str = "42", channel_id: str = "7"
) -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command="unusedemotes",
        args=args or [],
        author_id=author_id,
        author_name="Admin",
        channel_id=channel_id,
        channel_name="channel",
        content="!unusedemotes " + " ".join(args or []),
        reply=reply,
    )
    return ctx, replies


def make_module(settings, tmp_path, *, config=None, admin: bool = True):
    permissions = PermissionService(["42"] if admin else [])
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": {
                "url_prefix": "https://reports.example.com",
                "reports_dir": str(tmp_path),
                **(config or {}),
            },
            "router": EventRouter(),
            "services": Services(database=object(), permissions=permissions),
            "log": logging.getLogger("test.unusedemotes"),
        },
    )()
    return UnusedEmotesModule(context)


async def prepare(module, http: FakeSevenTV, totals=None):
    await module.setup()
    module._http = http
    messages = FakeMessages(totals)
    module._messages = messages
    return messages


async def test_admin_can_generate_report_in_channel_folder(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    http = FakeSevenTV()
    messages = await prepare(
        module,
        http,
        {build_patterns("Kappa")[0]: 2, build_patterns("Pog.Champ")[0]: 99},
    )
    ctx, replies = make_ctx(["7d", "10"])

    await module.on_unusedemotes(ctx)

    target: Path = tmp_path / "testchannel" / "unusedemotes.html"
    assert target.is_file()
    html = target.read_text(encoding="utf-8")
    assert "Kappa" in html
    assert "Pog.Champ" not in html
    assert "&#10004; All emotes meet the threshold!" not in html
    assert 'src="https://cdn.7tv.app/emote/abc/2x.webp"' in html
    assert replies == [
        "Generating report. This will take a while, please wait...",
        "Report generated, view at https://reports.example.com/testchannel/"
        "unusedemotes.html",
    ]
    assert all(call[0] == "7" for call in messages.calls)
    assert all("!ecount\\s+" in call[2] for call in messages.calls)
    assert all(
        datetime.now(UTC) - call[3] >= timedelta(days=7) - timedelta(seconds=5)
        for call in messages.calls
    )
    await module.stop()


async def test_channel_owner_is_allowed(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path, admin=False)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx(author_id="7")

    await module.on_unusedemotes(ctx)

    assert len(replies) == 2
    assert replies[-1].startswith("Report generated")
    await module.stop()


async def test_unauthorized_user_is_ignored(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path, admin=False)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx(author_id="99")

    await module.on_unusedemotes(ctx)

    assert replies == []
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


@pytest.mark.parametrize(
    "args",
    [["nonsense"], ["7d", "abc"], ["7d", "0"], ["7d", "10", "extra"]],
)
async def test_invalid_arguments_reply_with_usage(settings, tmp_path, args) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx(args)

    await module.on_unusedemotes(ctx)

    assert replies == ["Usage: !unusedemotes [cutoff] [threshold] (e.g. 6m 10)."]
    await module.stop()


async def test_missing_url_prefix_is_reported_before_generation(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path, config={"url_prefix": ""})
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx()

    await module.on_unusedemotes(ctx)

    assert replies == ["Report URL is not configured."]
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_channel_without_7tv_set_is_reported(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV(set_id=None))
    ctx, replies = make_ctx()

    await module.on_unusedemotes(ctx)

    assert replies[-1] == "No 7TV emote set found for this channel."
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_api_failure_replies_with_error(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV(error=SevenTVError("boom")))
    ctx, replies = make_ctx()

    await module.on_unusedemotes(ctx)

    assert replies[-1] == "Error generating report."
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_only_one_report_per_channel_runs_at_a_time(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    gate = asyncio.Event()
    await prepare(module, FakeSevenTV(gate=gate))
    first, first_replies = make_ctx()
    second, second_replies = make_ctx()

    task = asyncio.create_task(module.on_unusedemotes(first))
    for _ in range(5):
        await asyncio.sleep(0)
    await module.on_unusedemotes(second)

    assert second_replies == ["A report is already being generated for this channel."]
    gate.set()
    await task
    assert first_replies[-1].endswith("unusedemotes.html")
    await module.stop()
