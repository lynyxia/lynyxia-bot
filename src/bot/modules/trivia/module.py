"""Trivia game: ``!trivia``, ``!hint`` and ``!pass``.

Questions come from ``category_*.json`` files in the mounted trivia directory.
Sessions are per channel (this bot serves several), answers are checked on the
normal ``message`` event, and all output goes through ``ctx.reply`` so the
shared send queue and per-channel output gate still apply.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from bot.core.event_router import ChatMessageEvent, CommandContext
from bot.modules.base import Module, ModuleContext
from bot.modules.trivia.config import TriviaConfig
from bot.modules.trivia.questions import (
    TriviaQuestion,
    answers_match,
    load_categories,
    normalize_answer,
    normalize_category,
)

BOOL_ANSWERS = frozenset({"true", "false"})
Responder = Callable[[str], Awaitable[None]]


@dataclass
class TriviaSession:
    question: TriviaQuestion
    options: list[str]
    started_at: float
    starter: str
    responder: Responder
    answered: bool = False
    timer: asyncio.Task[None] | None = None


class TriviaModule(Module):
    name = "trivia"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = TriviaConfig.model_validate(context.config)
        self._sessions: dict[str, TriviaSession] = {}
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        self.subscribe(self.context.router.on_command("trivia", self.on_trivia))
        self.subscribe(self.context.router.on_command("hint", self.on_hint))
        self.subscribe(self.context.router.on_command("pass", self.on_pass))
        self.subscribe(self.context.router.on_event("message", self.on_message))

    async def stop(self) -> None:
        for session in list(self._sessions.values()):
            self._cancel_timer(session)
        self._sessions.clear()
        await super().stop()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    async def on_trivia(self, ctx: CommandContext) -> None:
        # Reload on each command: adding a JSON file to the mounted volume is
        # enough, no bot restart needed.
        categories = await asyncio.to_thread(load_categories, self.config)
        if not categories:
            await ctx.reply("No trivia questions found.")
            return

        available = ", ".join(
            sorted(category.name for category in categories.values())
        )
        args = [arg.strip() for arg in ctx.args if arg.strip()]
        if args and args[0].casefold() == "categories":
            await ctx.reply(f"Available trivia categories: {available}")
            return

        requested = " ".join(args)
        if requested:
            category = categories.get(normalize_category(requested))
            if category is None:
                await ctx.reply(
                    f"No trivia category found for '{requested}'. "
                    f"Available categories: {available}"
                )
                return
        else:
            category = categories[random.choice(list(categories))]

        question = random.choice(category.questions)
        options = self._options(question)

        async with self._lock:
            if ctx.channel_id in self._sessions:
                session = None
            else:
                session = TriviaSession(
                    question=question,
                    options=options,
                    started_at=time.monotonic(),
                    starter=ctx.author_login or ctx.author_name or ctx.author_id,
                    responder=ctx.reply,
                )
                self._sessions[ctx.channel_id] = session
        if session is None:
            await ctx.reply(
                "A trivia session is already running in this channel! "
                "Type !hint if you need help."
            )
            return

        intro = (
            f"Trivia time! You have {int(self.config.timeout_seconds)} seconds "
            f"to answer. | {question.question}"
        )
        if question.type == "boolean":
            text = f"{intro} | (True or False?)"
        else:
            text = (
                f"{intro} | Type !hint after "
                f"{int(self.config.hint_delay_seconds)} seconds if you need help."
            )
        session.timer = asyncio.create_task(
            self._expire(ctx.channel_id, session),
            name=f"trivia-timeout:{ctx.channel_id}",
        )
        try:
            await ctx.reply(text)
        except Exception:
            self._cancel_timer(session)
            async with self._lock:
                if self._sessions.get(ctx.channel_id) is session:
                    self._sessions.pop(ctx.channel_id, None)
            self.log.warning("Failed to post trivia question in channel %s", ctx.channel_id)

    async def on_hint(self, ctx: CommandContext) -> None:
        session = self._sessions.get(ctx.channel_id)
        if session is None or session.answered:
            await ctx.reply(
                "No trivia session is currently active. Type !trivia to start one!"
            )
            return

        elapsed = time.monotonic() - session.started_at
        if elapsed < self.config.hint_delay_seconds:
            remaining = max(1, math.ceil(self.config.hint_delay_seconds - elapsed))
            left = max(1, math.ceil(self.config.timeout_seconds - elapsed))
            await ctx.reply(
                f"Only {left} seconds left! Hint will be available in {remaining} seconds."
            )
            return

        if session.question.type == "boolean":
            await ctx.reply("A hint is not available for this question.")
            return

        options = " | ".join(f'"{option}"' for option in session.options)
        await ctx.reply(f"Hint: {options}")

    async def on_pass(self, ctx: CommandContext) -> None:
        session = self._sessions.get(ctx.channel_id)
        if session is None or session.answered:
            await ctx.reply(
                "No trivia session is currently active. Type !trivia to start one!"
            )
            return

        name = ctx.author_login or ctx.author_name or ctx.author_id
        if name.casefold() != session.starter.casefold():
            await ctx.reply("Only the person who started this trivia session can skip.")
            return

        async with self._lock:
            if self._sessions.get(ctx.channel_id) is not session or session.answered:
                return
            session.answered = True
            self._sessions.pop(ctx.channel_id, None)
        self._cancel_timer(session)
        await ctx.reply(
            f"@{name} skipped the current trivia question! "
            f"The correct answer was: {session.question.correct_answer}"
        )

    # ------------------------------------------------------------------
    # Answer checking
    # ------------------------------------------------------------------
    async def on_message(self, event: ChatMessageEvent) -> None:
        if not event.content or event.content.startswith("!"):
            return
        text = event.content.lstrip().lstrip("\U000e0000").lstrip()
        bot_login = self.context.settings.twitch.bot_username
        if bot_login and re.match(
            rf"@({re.escape(bot_login)})\b", text, flags=re.IGNORECASE
        ):
            return

        session = self._sessions.get(event.channel_id)
        if session is None or session.answered:
            return

        guess = normalize_answer(text)
        correct = normalize_answer(session.question.correct_answer)
        is_correct, similarity = answers_match(
            guess, correct, self.config.similarity_threshold
        )
        is_bool_guess = guess in BOOL_ANSWERS
        if not is_correct and not (
            session.question.type == "boolean" and is_bool_guess
        ):
            return

        async with self._lock:
            # Another message may have answered while we were comparing.
            if self._sessions.get(event.channel_id) is not session or session.answered:
                return
            session.answered = True
            self._sessions.pop(event.channel_id, None)
        self._cancel_timer(session)

        name = event.author_login or event.author_name or event.author_id
        answer = session.question.correct_answer  # already HTML-unescaped
        if not is_correct:
            await session.responder(f"Wrong! @{name} has answered incorrectly")
            return
        if similarity < 1:
            await session.responder(
                f"Well done! @{name} got the correct answer: {answer} "
                f"({int(similarity * 100)}% match)"
            )
        else:
            await session.responder(
                f"Well done! @{name} got the correct answer: {answer}"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _options(question: TriviaQuestion) -> list[str]:
        if question.type == "boolean":
            return ["True", "False"]
        options = [question.correct_answer, *question.incorrect_answers]
        random.shuffle(options)
        return options

    @staticmethod
    def _cancel_timer(session: TriviaSession) -> None:
        timer = session.timer
        if timer is not None and not timer.done():
            timer.cancel()
        session.timer = None

    async def _expire(self, channel_id: str, session: TriviaSession) -> None:
        try:
            await asyncio.sleep(self.config.timeout_seconds)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._sessions.get(channel_id) is not session or session.answered:
                return
            session.answered = True
            self._sessions.pop(channel_id, None)
        try:
            await session.responder(
                f"Time's up! The correct answer was: {session.question.correct_answer}"
            )
        except Exception:
            self.log.warning("Failed to post trivia timeout in channel %s", channel_id)
