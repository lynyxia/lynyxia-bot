"""Tests for the passive pyramid watcher."""

from __future__ import annotations

import logging

import pytest

from bot.core.event_router import ChatMessageEvent, EventRouter
from bot.modules.pyramid.detector import ChatLine, Outcome, PyramidDetector, parse_chat_line
from bot.modules.pyramid.messages import reaction_text
from bot.modules.pyramid.module import PyramidModule
from bot.services import Services
from bot.services.live_status import LiveStatusService


def line(
    content: str, *, author: str = "Alice", user_id: str = "1"
) -> ChatLine:
    return parse_chat_line(content, author, user_id)


def feed_pyramid(
    detector: PyramidDetector,
    width: int,
    *,
    emote: str = "Kappa",
    starter: tuple[str, str] = ("Alice", "1"),
    middle: tuple[str, str] | None = None,
    finisher: tuple[str, str] | None = None,
) -> Outcome:
    """Feed a complete pyramid and return the final outcome."""
    for count in range(1, width + 1):
        outcome = detector.feed(
            ChatLine(emote, count, starter[0], starter[1])
        )
    for count in range(width - 1, 0, -1):
        who = finisher if count == 1 and finisher is not None else (middle or starter)
        outcome = detector.feed(ChatLine(emote, count, who[0], who[1]))
    return outcome


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Kappa", ("Kappa", 1)),
        ("Kappa Kappa Kappa", ("Kappa", 3)),
        ("kappa kappa", ("kappa", 2)),
        ("Kappa kappa", (None, 0)),
        ("Kappa Kappa!", (None, 0)),
        ("hello world", (None, 0)),
        ("", (None, 0)),
    ],
)
def test_parse_chat_line(content: str, expected: tuple[str | None, int]) -> None:
    result = line(content)
    assert (result.emote, result.width) == expected


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (
            3,
            "@Alice has built a 3-wide Kappa pyramid. "
            "That's not a pyramid, surely you can do better!",
        ),
        (
            4,
            "@Alice has built a 4-wide Kappa pyramid. "
            "That's not a pyramid, surely you can do better!",
        ),
        (
            5,
            "@Alice has built a 5-wide Kappa pyramid. "
            "Tiny pyramid detected. The Pharaoh is disappointed, @Alice.",
        ),
        (
            7,
            "@Alice has built a 7-wide Kappa pyramid. "
            "Tiny pyramid detected. The Pharaoh is disappointed, @Alice.",
        ),
        (
            8,
            "@Alice has built a 8-wide Kappa pyramid. "
            "A respectable pyramid. Somewhere, an archaeologist just smiled, @Alice.",
        ),
        (
            11,
            "@Alice has built a 11-wide Kappa pyramid. "
            "A respectable pyramid. Somewhere, an archaeologist just smiled, @Alice.",
        ),
        (
            12,
            "@Alice has built a 12-wide Kappa pyramid! "
            "Now THAT'S a pyramid! The Egyptians would be proud, @Alice.",
        ),
        (
            15,
            "@Alice has built a 15-wide Kappa pyramid! "
            "Now THAT'S a pyramid! The Egyptians would be proud, @Alice.",
        ),
        (
            16,
            "@Alice has built a 16-wide Kappa pyramid!! "
            "Now that's a BIG pyramid! The Pharaoh is considering giving you a "
            "promotion. 👑",
        ),
        (
            24,
            "@Alice has built a 24-wide Kappa pyramid!! "
            "Now that's a BIG pyramid! The Pharaoh is considering giving you a "
            "promotion. 👑",
        ),
        (
            25,
            "@Alice has built a 25-wide Kappa pyramid!!! "
            "HOLY PYRAMIDS!!! We found the eighth wonder of the world!!!.",
        ),
        (
            30,
            "@Alice has built a 30-wide Kappa pyramid!!! "
            "HOLY PYRAMIDS!!! We found the eighth wonder of the world!!!.",
        ),
    ],
)
def test_completed_pyramid_matches_its_width_band(width: int, expected: str) -> None:
    outcome = feed_pyramid(PyramidDetector(), width)
    assert outcome.kind == "complete"
    assert outcome.width == width
    assert outcome.emote == "Kappa"
    assert outcome.stolen is False
    assert reaction_text(outcome) == expected


def test_pyramid_below_minimum_width_gets_no_reaction() -> None:
    outcome = feed_pyramid(PyramidDetector(), 2)
    assert outcome.kind == "none"


def test_middle_contributors_do_not_make_it_stolen() -> None:
    detector = PyramidDetector()
    outcome = feed_pyramid(
        detector,
        4,
        starter=("Alice", "1"),
        middle=("Bob", "2"),
        finisher=("Alice", "1"),
    )
    assert outcome.stolen is False
    assert "@Alice" in reaction_text(outcome)
    assert "SipTime" not in reaction_text(outcome)


def test_different_last_author_steals_the_pyramid() -> None:
    outcome = feed_pyramid(
        PyramidDetector(),
        4,
        starter=("Alice", "1"),
        middle=("Bob", "2"),
        finisher=("Carol", "3"),
    )
    assert outcome.stolen is True
    assert reaction_text(outcome) == (
        "SipTime Well that was rude! @Carol just stole a 4 pyramid from @Alice!"
    )


def test_deviation_on_the_way_up_resets_silently() -> None:
    detector = PyramidDetector()
    for count in (1, 2, 3):
        assert detector.feed(line("Kappa " * count)).kind == "none"
    assert detector.feed(line("unrelated chatter")).kind == "none"
    assert detector.feed(line("Kappa " * 4)).kind == "none"

    outcome = feed_pyramid(detector, 3)
    assert outcome.kind == "complete"


def test_repeated_count_on_the_way_up_resets_silently() -> None:
    detector = PyramidDetector()
    detector.feed(line("Kappa"))
    detector.feed(line("Kappa Kappa"))
    assert detector.feed(line("Kappa Kappa")).kind == "none"
    assert detector.feed(line("Kappa Kappa Kappa")).kind == "none"


def test_deviation_after_peak_collapses_and_names_breaker() -> None:
    detector = PyramidDetector()
    for count in (1, 2, 3, 4, 3):
        detector.feed(line("Kappa " * count))
    outcome = detector.feed(line("hello there", author="Mallory", user_id="9"))
    assert outcome.kind == "collapse"
    assert reaction_text(outcome) == (
        "FailFish The pyramid has collapsed thanks to @Mallory! You have been cursed."
    )


def test_wrong_count_after_peak_also_collapses() -> None:
    detector = PyramidDetector()
    for count in (1, 2, 3, 4, 3):
        detector.feed(line("Kappa " * count))
    outcome = detector.feed(line("Kappa Kappa Kappa Kappa", author="Mallory", user_id="9"))
    assert outcome.kind == "collapse"
    assert outcome.breaker == "Mallory"


@pytest.mark.parametrize(
    "intruder",
    [
        "Kappa",  # too early in the descent
        "FailFish",  # a 1-wide line of a different emote
    ],
)
def test_one_wide_lines_never_collapse(intruder: str) -> None:
    detector = PyramidDetector()
    for count in (1, 2, 3, 4, 3):
        detector.feed(line("Kappa " * count))
    assert detector.feed(line(intruder, author="Mallory", user_id="9")).kind == "none"


def test_case_change_breaks_the_pyramid() -> None:
    detector = PyramidDetector()
    for count in (1, 2, 3):
        detector.feed(line("Kappa " * count))
    assert detector.feed(line("kappa " * 3)).kind == "none"


class FakeTwitch:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, channel_id: str, content: str, **_: object) -> None:
        self.sent.append((channel_id, content))


def make_module(settings, *, twitch: FakeTwitch, status: LiveStatusService, **config):
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": config,
            "router": EventRouter(),
            "services": Services(twitch=twitch, live_status=status),
            "log": logging.getLogger("test.pyramid"),
        },
    )()
    return PyramidModule(context)


def event(
    content: str, *, author: str = "Alice", user_id: str = "1", channel: str = "7"
) -> ChatMessageEvent:
    return ChatMessageEvent(
        author_id=user_id,
        author_login=author.lower(),
        author_name=author,
        channel_id=channel,
        channel_login="channel",
        channel_name="channel",
        content=content,
        message_id="1",
        sent_at=None,
    )


async def play(module: PyramidModule, width: int, *, channel: str = "7") -> None:
    router = module.context.router
    for count in range(1, width + 1):
        await router.dispatch_event("message", event("Kappa " * count, channel=channel))
    for count in range(width - 1, 0, -1):
        await router.dispatch_event("message", event("Kappa " * count, channel=channel))


async def test_module_reacts_through_twitch_service(settings) -> None:
    twitch = FakeTwitch()
    module = make_module(settings, twitch=twitch, status=LiveStatusService())
    await module.setup()

    await play(module, 4)

    assert twitch.sent == [
        (
            "7",
            "@Alice has built a 4-wide Kappa pyramid. "
            "That's not a pyramid, surely you can do better!",
        )
    ]
    await module.stop()


async def test_channels_are_isolated(settings) -> None:
    twitch = FakeTwitch()
    module = make_module(settings, twitch=twitch, status=LiveStatusService())
    await module.setup()

    # Interleave two channels; neither should see the other's lines.
    for count in (1, 2, 3):
        await module.context.router.dispatch_event(
            "message", event("Kappa " * count, channel="7")
        )
        await module.context.router.dispatch_event(
            "message", event("Kappa " * count, channel="8")
        )
    for count in (2, 1):
        await module.context.router.dispatch_event(
            "message", event("Kappa " * count, channel="7")
        )
        await module.context.router.dispatch_event(
            "message", event("Kappa " * count, channel="8")
        )

    assert len(twitch.sent) == 2
    assert {channel for channel, _ in twitch.sent} == {"7", "8"}
    await module.stop()


async def test_reactions_are_suppressed_while_live(settings) -> None:
    twitch = FakeTwitch()
    status = LiveStatusService()
    status.set_live("7", True)
    module = make_module(settings, twitch=twitch, status=status)
    await module.setup()

    await play(module, 4)
    assert twitch.sent == []

    status.set_live("7", False)
    await play(module, 4)
    assert len(twitch.sent) == 1
    await module.stop()


async def test_disabled_module_registers_no_handler(settings) -> None:
    twitch = FakeTwitch()
    module = make_module(
        settings, twitch=twitch, status=LiveStatusService(), enabled=False
    )
    await module.setup()

    await play(module, 4)

    assert twitch.sent == []
    assert module.context.router._events.get("message") is None
