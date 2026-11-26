"""Tests for the trivia module: category loading, sessions and unescaping."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from bot.core.event_router import ChatMessageEvent, CommandContext, EventRouter
from bot.modules.trivia.module import TriviaModule
from bot.modules.trivia.questions import normalize_answer, parse_question
from bot.services import Services

SAMPLE = [
    {
        "type": "multiple",
        "difficulty": "hard",
        "category": "Sample",
        "question": "Who won the &quot;Dino Run&#039;s Invasion&quot; tournament?",
        "correct_answer": "Jumping Lizards",
        "incorrect_answers": ["Ramsay&#039;s Kitchen", "Andr&eacute;s Team"],
    }
]


_CREATED: list[TriviaModule] = []


@pytest.fixture(autouse=True)
async def _stop_created_modules():
    """Cancel pending timeout tasks left behind by a test."""
    _CREATED.clear()
    yield
    for module in list(_CREATED):
        await module.stop()
    _CREATED.clear()


def write_category(tmp_path, name: str, questions: object) -> None:
    (tmp_path / name).write_text(json.dumps(questions), encoding="utf-8")


def make_module(settings, tmp_path, **config):
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": {"directory": str(tmp_path), **config},
            "router": EventRouter(),
            "services": Services(),
            "log": logging.getLogger("test.trivia"),
        },
    )()
    module = TriviaModule(context)
    _CREATED.append(module)
    return module


def make_ctx(command, args=(), *, replies=None, author="chatter", channel="7"):
    replies = replies if replies is not None else []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command=command,
        args=list(args),
        author_id="99",
        author_name=author,
        author_login=author,
        channel_id=channel,
        channel_name="channel",
        content="!" + command + " " + " ".join(args),
        reply=reply,
    )
    return ctx, replies


def make_event(content, *, author="chatter", channel="7"):
    return ChatMessageEvent(
        author_id="99",
        author_login=author,
        author_name=author,
        channel_id=channel,
        channel_login="channel",
        channel_name="channel",
        content=content,
        message_id="1",
        sent_at=None,
    )


def test_question_fields_are_html_unescaped() -> None:
    question = parse_question(SAMPLE[0])
    assert question is not None
    assert "Dino Run's Invasion" in question.question
    assert "Ramsay's Kitchen" in question.incorrect_answers
    assert "Andrés Team" in question.incorrect_answers
    assert "&quot;" not in repr(question)
    assert "&#039;" not in repr(question)
    assert "&eacute;" not in repr(question)


def test_normalize_answer_strips_entities_and_quotes() -> None:
    assert normalize_answer('"&quot;Dino Run&#039;s Invasion&quot;"') == (
        "dino run's invasion"
    )
    assert normalize_answer("&eacute;") == "é"


def test_invalid_questions_are_skipped(tmp_path, settings) -> None:
    write_category(
        tmp_path,
        "category_Mixed.json",
        [SAMPLE[0], {"type": "other"}, {"question": "no answer"}, "not an object"],
    )
    (tmp_path / "category_Broken.json").write_text("{oops", encoding="utf-8")
    module = make_module(settings, tmp_path)

    from bot.modules.trivia.questions import load_categories

    categories = load_categories(module.config)
    assert list(categories) == ["mixed"]
    assert len(categories["mixed"].questions) == 1


async def test_categories_command_and_unknown_category(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    write_category(tmp_path, "category_Animals.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()

    ctx, replies = make_ctx("trivia", ["categories"])
    await module.on_trivia(ctx)

    assert replies == ["Available trivia categories: Animals, Sample"]

    ctx, replies = make_ctx("trivia", ["nope"])
    await module.on_trivia(ctx)

    assert replies[0].startswith("No trivia category found for 'nope'.")
    assert "Animals, Sample" in replies[0]


async def test_starting_question_is_unescaped(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()

    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)

    assert len(replies) == 1
    assert "Dino Run's Invasion" in replies[0]
    assert "&quot;" not in replies[0]
    assert "&#039;" not in replies[0]
    assert "7" in module._sessions


async def test_hint_before_delay_and_with_options(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path, hint_delay_seconds=10)
    await module.setup()
    start_ctx, _replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(start_ctx)

    early_ctx, early = make_ctx("hint")
    await module.on_hint(early_ctx)
    assert early[0].startswith("Only ")
    assert "Hint will be available in" in early[0]

    module.config.hint_delay_seconds = 0
    hint_ctx, hints = make_ctx("hint")
    await module.on_hint(hint_ctx)
    assert "Ramsay's Kitchen" in hints[0]
    assert "Andrés Team" in hints[0]
    assert "&eacute;" not in hints[0]


async def test_boolean_question_and_hint(settings, tmp_path) -> None:
    write_category(
        tmp_path,
        "category_Bool.json",
        [
            {
                "type": "boolean",
                "question": "Is this &quot;True&quot;?",
                "correct_answer": "True",
                "incorrect_answers": [],
            }
        ],
    )
    module = make_module(settings, tmp_path, hint_delay_seconds=0)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["bool"])
    await module.on_trivia(ctx)

    assert "(True or False?)" in replies[0]
    assert "&quot;" not in replies[0]

    hint_ctx, hints = make_ctx("hint")
    await module.on_hint(hint_ctx)
    assert hints == ["A hint is not available for this question."]


async def test_correct_answer_ends_session(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)

    await module.on_message(make_event("Jumping Lizards"))

    assert replies[-1] == "Well done! @chatter got the correct answer: Jumping Lizards"
    assert "7" not in module._sessions


async def test_fuzzy_answer_reports_match_percentage(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)

    await module.on_message(make_event("Jumping Lizard"))

    assert "got the correct answer: Jumping Lizards" in replies[-1]
    assert "% match)" in replies[-1]


async def test_boolean_wrong_answer_ends_round(settings, tmp_path) -> None:
    write_category(
        tmp_path,
        "category_Bool.json",
        [
            {
                "type": "boolean",
                "question": "Is this true?",
                "correct_answer": "True",
                "incorrect_answers": [],
            }
        ],
    )
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["bool"])
    await module.on_trivia(ctx)

    await module.on_message(make_event("False"))

    assert replies[-1] == "Wrong! @chatter has answered incorrectly"
    assert "7" not in module._sessions


async def test_wrong_guess_keeps_multiple_choice_round_open(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)
    after_start = len(replies)

    await module.on_message(make_event("Totally wrong"))
    assert len(replies) == after_start
    assert "7" in module._sessions

    await module.on_message(make_event("Jumping Lizards"))
    assert "Well done!" in replies[-1]


async def test_html_entities_in_guess_are_normalized(settings, tmp_path) -> None:
    write_category(
        tmp_path,
        "category_Sample.json",
        [
            {
                "type": "multiple",
                "question": "Who?",
                "correct_answer": "Andrés Manuel",
                "incorrect_answers": ["Someone Else"],
            }
        ],
    )
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)

    await module.on_message(make_event("Andr&eacute;s Manuel"))

    assert "got the correct answer: Andrés Manuel" in replies[-1]


async def test_pass_permissions_and_reveal(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"], author="starter")
    await module.on_trivia(ctx)

    other_ctx, denied = make_ctx("pass", author="someoneelse")
    await module.on_pass(other_ctx)
    assert denied == ["Only the person who started this trivia session can skip."]
    assert "7" in module._sessions

    pass_ctx, passed = make_ctx("pass", author="STARTER")
    await module.on_pass(pass_ctx)
    assert "skipped the current trivia question" in passed[0]
    assert "Jumping Lizards" in passed[0]
    assert "7" not in module._sessions


async def test_sessions_are_isolated_per_channel(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx7, _ = make_ctx("trivia", ["sample"], channel="7")
    ctx8, replies8 = make_ctx("trivia", ["sample"], channel="8")
    await module.on_trivia(ctx7)
    await module.on_trivia(ctx8)

    assert set(module._sessions) == {"7", "8"}

    await module.on_message(make_event("Jumping Lizards", channel="7"))
    assert "7" not in module._sessions
    assert "8" in module._sessions
    assert replies8 == [
        'Trivia time! You have 60 seconds to answer. | '
        'Who won the "Dino Run\'s Invasion" tournament? | '
        'Type !hint after 10 seconds if you need help.'
    ]


async def test_second_trivia_in_same_channel_is_rejected(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    first, _ = make_ctx("trivia", ["sample"])
    await module.on_trivia(first)
    second, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(second)

    assert "already running" in replies[0]


async def test_timeout_reveals_answer_and_is_cancelled_on_answer(
    settings, tmp_path
) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path, timeout_seconds=0.05)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)

    await module.on_message(make_event("Jumping Lizards"))
    await asyncio.sleep(0.15)

    assert not any("Time's up" in text for text in replies)

    ctx2, replies2 = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx2)
    await asyncio.sleep(0.15)
    assert any("Time's up! The correct answer was: Jumping Lizards" in text for text in replies2)


async def test_no_questions_and_no_active_session(settings, tmp_path) -> None:
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia")
    await module.on_trivia(ctx)
    assert replies == ["No trivia questions found."]

    hint_ctx, hint_replies = make_ctx("hint")
    await module.on_hint(hint_ctx)
    pass_ctx, pass_replies = make_ctx("pass")
    await module.on_pass(pass_ctx)
    assert hint_replies == pass_replies == [
        "No trivia session is currently active. Type !trivia to start one!"
    ]


async def test_commands_and_bot_mentions_are_not_answers(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)
    after_start = len(replies)

    await module.on_message(make_event("!hint"))
    await module.on_message(make_event("@testbot Jumping Lizards"))

    assert len(replies) == after_start
    assert "7" in module._sessions


async def test_new_files_are_picked_up_without_restart(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()

    write_category(tmp_path, "category_Animals.json", SAMPLE)
    ctx, replies = make_ctx("trivia", ["categories"])
    await module.on_trivia(ctx)

    assert replies == ["Available trivia categories: Animals, Sample"]


async def test_stop_cancels_pending_timers(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    module = make_module(settings, tmp_path, timeout_seconds=0.05)
    await module.setup()
    ctx, replies = make_ctx("trivia", ["sample"])
    await module.on_trivia(ctx)

    await module.stop()
    await asyncio.sleep(0.15)

    assert not any("Time's up" in text for text in replies)


async def test_bare_trivia_picks_a_random_category(settings, tmp_path) -> None:
    write_category(tmp_path, "category_Sample.json", SAMPLE)
    write_category(tmp_path, "category_Animals.json", SAMPLE)
    module = make_module(settings, tmp_path)
    await module.setup()

    ctx, replies = make_ctx("trivia")
    await module.on_trivia(ctx)

    assert len(replies) == 1
    assert replies[0].startswith("Trivia time!")
    assert "Dino Run's Invasion" in replies[0]
    assert "7" in module._sessions


async def test_revealed_answer_is_html_unescaped(settings, tmp_path) -> None:
    write_category(
        tmp_path,
        "category_Sample.json",
        [
            {
                "type": "multiple",
                "question": "Who?",
                "correct_answer": "Andr&eacute;s Manuel L&oacute;pez",
                "incorrect_answers": ["Someone Else"],
            }
        ],
    )
    module = make_module(settings, tmp_path)
    await module.setup()
    start_ctx, _ = make_ctx("trivia", ["sample"])
    await module.on_trivia(start_ctx)

    pass_ctx, replies = make_ctx("pass")
    await module.on_pass(pass_ctx)

    assert "Andrés Manuel López" in replies[0]
    assert "&eacute;" not in replies[0]
    assert "&oacute;" not in replies[0]
