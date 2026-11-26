"""Tests for the admin-only 7TV emote-set comparison module."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.core.event_router import CommandContext, EventRouter
from bot.core.permissions import PermissionService
from bot.modules.emote_sets.compare import (
    EmoteSet,
    build_comparisons,
    build_index,
    emote_details,
    render_report,
)
from bot.modules.emote_sets.config import EmoteSetsConfig
from bot.modules.emote_sets.module import EmoteSetsModule
from bot.services import Services
from bot.services.seven_tv import SevenTVError

OWNED = [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}]
FULL = {
    "a": {
        "id": "a",
        "name": "Alpha",
        "emotes": [
            {
                "id": "1",
                "name": "Kappa",
                "host": {
                    "url": "//cdn.7tv.app/emote/1",
                    "files": [{"name": "2x.webp"}],
                },
            },
            {
                "id": "2",
                "name": "OnlyA",
                "host": {
                    "url": "//cdn.7tv.app/emote/2",
                    "files": [{"name": "2x.webp"}],
                },
            },
        ],
    },
    "b": {
        "id": "b",
        "name": "Beta",
        "emotes": [
            {"id": "1", "name": "Kappa"},
            {"id": "3", "name": "OnlyB", "animated": True, "tags": ["x", "y"]},
        ],
    },
}


def sets_from(full: dict[str, dict]) -> list[EmoteSet]:
    return [
        EmoteSet(id=item["id"], name=item["name"], emotes=item.get("emotes") or [])
        for item in full.values()
    ]


def test_config_validates_filename_and_cap() -> None:
    config = EmoteSetsConfig()
    assert config.filename == "emotesets.html"
    assert config.max_sets == 10
    with pytest.raises(ValidationError):
        EmoteSetsConfig(filename="../bad.html")
    with pytest.raises(ValidationError):
        EmoteSetsConfig(max_sets=1)


def test_build_index_and_emote_details() -> None:
    index = build_index(FULL["b"]["emotes"])
    assert set(index) == {"Kappa", "OnlyB"}
    assert emote_details(index["Kappa"]) == "Static"
    assert emote_details(index["OnlyB"]) == "Animated, 2 tags"


def test_comparisons_find_missing_emotes_both_ways() -> None:
    comparisons = build_comparisons(sets_from(FULL))
    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert [name for name, _ in comparison.missing_from_b] == ["OnlyA"]
    assert [name for name, _ in comparison.missing_from_a] == ["OnlyB"]


def test_report_lists_both_directions_and_escapes_values() -> None:
    comparisons = build_comparisons(sets_from(FULL))
    html = render_report(comparisons, sets_from(FULL), "<channel>", False)
    assert "OnlyA" in html
    assert "OnlyB" in html
    assert "Animated, 2 tags" in html
    assert 'src="https://cdn.7tv.app/emote/2/2x.webp"' in html
    assert "&lt;channel&gt;" in html
    assert "Qwen 3.6 35B" in html
    assert "Only the first few emote sets" not in html


def test_report_notes_truncation_and_matching_sets() -> None:
    identical = {
        "a": {"id": "a", "name": "Alpha", "emotes": [{"id": "1", "name": "Kappa"}]},
        "b": {"id": "b", "name": "Beta", "emotes": [{"id": "2", "name": "Kappa"}]},
    }
    html = render_report(
        build_comparisons(sets_from(identical)), sets_from(identical), "chan", True
    )
    assert "Only the first few emote sets" in html
    assert "All emotes match!" in html


def test_malicious_emote_names_are_escaped() -> None:
    malicious = {
        "a": {"id": "a", "name": "Alpha", "emotes": [
            {"id": "1", "name": '<script>alert("x")</script>',
             "host": {"url": "javascript:alert(1)", "files": []}},
        ]},
        "b": {"id": "b", "name": "Beta", "emotes": []},
    }
    html = render_report(
        build_comparisons(sets_from(malicious)), sets_from(malicious), "chan", False
    )
    assert "<script>" not in html
    assert "javascript:" not in html
    assert "&lt;script&gt;" in html


class FakeSevenTV:
    def __init__(
        self,
        *,
        owned: list[dict] | None = None,
        full: dict[str, dict] | None = None,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.owned = OWNED if owned is None else owned
        self.full = FULL if full is None else full
        self.error = error
        self.gate = gate

    async def owned_emote_sets(self, twitch_id: str) -> list[dict]:
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.owned

    async def emote_set(self, emote_set_id: str) -> dict:
        return self.full[emote_set_id]


def make_ctx(
    args: list[str] | None = None, *, author_id: str = "42", channel_id: str = "7"
) -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command="emotesets",
        args=args or [],
        author_id=author_id,
        author_name="Admin",
        channel_id=channel_id,
        channel_name="channel",
        content="!emotesets " + " ".join(args or []),
        reply=reply,
    )
    return ctx, replies


def make_module(
    settings,
    tmp_path,
    *,
    config=None,
    admin: bool = True,
    url_prefix: str = "https://reports.example.com",
):
    permissions = PermissionService(["42"] if admin else [])
    reports = settings.reports.model_copy(
        update={"url_prefix": url_prefix, "reports_dir": str(tmp_path)}
    )
    local_settings = settings.model_copy(update={"reports": reports})
    context = type(
        "Context",
        (),
        {
            "settings": local_settings,
            "config": config or {},
            "router": EventRouter(),
            "services": Services(database=object(), permissions=permissions),
            "log": logging.getLogger("test.emotesets"),
        },
    )()
    return EmoteSetsModule(context)


async def prepare(module: EmoteSetsModule, http: FakeSevenTV) -> None:
    await module.setup()
    module._http = http


async def test_admin_can_generate_report_in_channel_folder(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx()

    await module.on_emotesets(ctx)

    target: Path = tmp_path / "testchannel" / "emotesets.html"
    assert target.is_file()
    html = target.read_text(encoding="utf-8")
    assert "OnlyA" in html
    assert "OnlyB" in html
    assert replies == [
        "Generating report, please wait...",
        "Report generated, view at https://reports.example.com/testchannel/"
        "emotesets.html",
    ]
    await module.stop()


async def test_channel_owner_is_allowed(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path, admin=False)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx(author_id="7")

    await module.on_emotesets(ctx)

    assert len(replies) == 2
    assert replies[-1].startswith("Report generated")
    await module.stop()


async def test_unauthorized_user_is_ignored(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path, admin=False)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx(author_id="99")

    await module.on_emotesets(ctx)

    assert replies == []
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_arguments_are_rejected(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx(["extra"])

    await module.on_emotesets(ctx)

    assert replies == ["Usage: !emotesets (no arguments)."]
    await module.stop()


async def test_missing_url_prefix_is_reported(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path, url_prefix="")
    await prepare(module, FakeSevenTV())
    ctx, replies = make_ctx()

    await module.on_emotesets(ctx)

    assert replies == ["Report URL is not configured."]
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_fewer_than_two_sets_is_reported(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV(owned=[{"id": "a", "name": "Alpha"}]))
    ctx, replies = make_ctx()

    await module.on_emotesets(ctx)

    assert replies[-1] == "Fewer than two 7TV emote sets found."
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_set_cap_is_applied(settings, tmp_path) -> None:
    owned = [
        {"id": "a", "name": "Alpha"},
        {"id": "b", "name": "Beta"},
        {"id": "c", "name": "Charlie"},
    ]
    module = make_module(settings, tmp_path, config={"max_sets": 2})
    await prepare(module, FakeSevenTV(owned=owned))
    ctx, _ = make_ctx()

    await module.on_emotesets(ctx)

    html = (tmp_path / "testchannel" / "emotesets.html").read_text(encoding="utf-8")
    assert "Charlie" not in html
    assert "Only the first few emote sets" in html
    await module.stop()


async def test_api_failure_replies_with_error(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await prepare(module, FakeSevenTV(error=SevenTVError("boom")))
    ctx, replies = make_ctx()

    await module.on_emotesets(ctx)

    assert replies[-1] == "Error generating report."
    assert not (tmp_path / "testchannel").exists()
    await module.stop()


async def test_only_one_report_per_type_runs_at_a_time(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    gate = asyncio.Event()
    await prepare(module, FakeSevenTV(gate=gate))
    first, first_replies = make_ctx()
    second, second_replies = make_ctx()

    task = asyncio.create_task(module.on_emotesets(first))
    for _ in range(5):
        await asyncio.sleep(0)
    await module.on_emotesets(second)

    assert second_replies == ["A report is already being generated for this channel."]
    gate.set()
    await task
    assert first_replies[-1].endswith("emotesets.html")
    await module.stop()
