"""Pure pyramid detection state machine.

A pyramid is a run of chat messages that repeats one emote with widths
``1, 2, ..., W`` and then ``W-1, ..., 1``. This module has no Twitch or I/O
dependencies so the rules can be tested directly: the bot module turns the
returned ``Outcome`` into a chat message.

Two details matter:

* Only the first and last lines matter for theft — other people may contribute
  in the middle without it counting as stolen.
* A 1-wide line never collapses a pyramid. It either finishes the descent or
  silently starts a fresh candidate, because single-emote messages are far too
  common in chat to treat as a collapse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["none", "complete", "collapse"]

IDLE = "idle"
ASCENDING = "ascending"
DESCENDING = "descending"


@dataclass(frozen=True)
class ChatLine:
    """One incoming chat message reduced to what detection needs."""

    # ``None`` when the message is not N copies of a single token.
    emote: str | None
    width: int
    author_name: str
    author_id: str


@dataclass(frozen=True)
class Outcome:
    """Result of feeding a single line to :class:`PyramidDetector`."""

    kind: Kind = "none"
    width: int = 0
    emote: str = ""
    starter: str = ""
    finisher: str = ""
    # Identity comparison uses stable Twitch IDs, not display names, so a
    # rename in the middle of a pyramid cannot look like theft.
    stolen: bool = False
    breaker: str = ""


def parse_chat_line(content: str, author_name: str, author_id: str) -> ChatLine:
    """Return a uniform emote line, or an unrelated line for anything else.

    Matching is deliberately case-sensitive: ``Kappa`` and ``kappa`` are
    different emotes for Twitch, so they are different pyramid lines too.
    """
    tokens = content.split()
    if not tokens:
        return ChatLine(None, 0, author_name, author_id)
    first = tokens[0]
    if any(token != first for token in tokens[1:]):
        return ChatLine(None, 0, author_name, author_id)
    return ChatLine(first, len(tokens), author_name, author_id)


class PyramidDetector:
    """Track one candidate pyramid for a single channel."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._phase: str = IDLE
        self._emote: str = ""
        self._expected: int = 0
        self._peak: int = 0
        self._starter_name: str = ""
        self._starter_id: str = ""

    def feed(self, line: ChatLine) -> Outcome:
        """Consume one chat line and report completion, collapse or nothing."""
        if self._phase == IDLE:
            self._start_candidate(line)
            return Outcome()

        # Single-emote lines never collapse anything.
        if line.emote is not None and line.width == 1:
            if (
                self._phase == DESCENDING
                and self._expected == 1
                and line.emote == self._emote
            ):
                return self._complete(line)
            self._start_candidate(line)
            return Outcome()

        if line.emote == self._emote and line.width > 1:
            if line.width == self._expected:
                # Still ascending, or continuing the descent.
                self._expected += 1 if self._phase == ASCENDING else -1
                return Outcome()
            if (
                self._phase == ASCENDING
                and self._expected >= 3
                and line.width == self._expected - 2
            ):
                # The previous line was the peak; this is the first step down.
                self._peak = self._expected - 1
                self._phase = DESCENDING
                self._expected = line.width - 1
                return Outcome()

        return self._deviation(line)

    def _start_candidate(self, line: ChatLine) -> None:
        """Start a new pyramid from a 1-wide line; otherwise reset silently."""
        if line.emote is not None and line.width == 1:
            self._phase = ASCENDING
            self._emote = line.emote
            self._expected = 2
            self._peak = 0
            self._starter_name = line.author_name
            self._starter_id = line.author_id
        else:
            self.reset()

    def _complete(self, line: ChatLine) -> Outcome:
        outcome = Outcome(
            kind="complete",
            width=self._peak,
            emote=self._emote,
            starter=self._starter_name,
            finisher=line.author_name,
            stolen=bool(line.author_id) and line.author_id != self._starter_id,
        )
        self.reset()
        return outcome

    def _deviation(self, line: ChatLine) -> Outcome:
        """Handle a non-1-wide line that does not continue the sequence."""
        if self._phase == DESCENDING:
            outcome = Outcome(kind="collapse", breaker=line.author_name)
            self.reset()
            return outcome
        # Deviations on the way up are normal chat noise; start over quietly.
        self.reset()
        return Outcome()
