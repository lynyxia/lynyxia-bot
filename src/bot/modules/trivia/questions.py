"""Load and normalize trivia question files.

Files are plain JSON lists of OpenTriviaDB-style objects. Every string is
HTML-unescaped here, once, so chat output never contains ``&quot;`` or
``&#039;``. Answer comparisons use the same normalization on the chatter's
guess, which also fixes names like ``Andr&eacute;s`` matching ``Andrés``.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from bot.modules.trivia.config import TriviaConfig

VALID_TYPES = frozenset({"multiple", "boolean"})


@dataclass(frozen=True)
class TriviaQuestion:
    category: str
    type: str
    question: str
    correct_answer: str
    incorrect_answers: tuple[str, ...]


@dataclass(frozen=True)
class Category:
    name: str
    questions: tuple[TriviaQuestion, ...]


def unescape(value: Any) -> Any:
    """Recursively HTML-unescape strings; leave other values untouched."""
    if isinstance(value, str):
        return html.unescape(value)
    if isinstance(value, list):
        return [unescape(item) for item in value]
    if isinstance(value, dict):
        return {key: unescape(item) for key, item in value.items()}
    return value


def normalize_category(value: str) -> str:
    """Fold spaces/underscores to dashes so ``video games`` finds Video-Games."""
    key = re.sub(r"[\s_]+", "-", value.strip().casefold())
    return re.sub(r"-+", "-", key).strip("-")


def normalize_answer(value: object) -> str:
    """HTML-unescape, drop wrapping quotes/markup and case-fold an answer."""
    text = unescape(str(value)).strip().strip("\U000e0000").strip()
    # Entities may themselves decode to quote characters, so peel wrappers
    # repeatedly (e.g. '"&quot;Name&quot;"' -> '"Name"' -> 'Name').
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return " ".join(text.casefold().split())


def answers_match(
    guess: str, correct: str, threshold: float
) -> tuple[bool, float]:
    """Return (is_correct, similarity) using a fuzzy whole-string comparison."""
    similarity = SequenceMatcher(None, correct, guess).ratio()
    return similarity >= threshold, similarity


def parse_question(raw: Any) -> TriviaQuestion | None:
    """Validate and unescape one question, or return ``None`` to skip it."""
    if not isinstance(raw, dict):
        return None
    question_type = str(raw.get("type", "multiple")).strip().casefold()
    if question_type not in VALID_TYPES:
        return None
    question = unescape(raw.get("question", ""))
    correct = unescape(raw.get("correct_answer", ""))
    if not isinstance(question, str) or not question.strip():
        return None
    if not isinstance(correct, str) or not correct.strip():
        return None
    raw_incorrect = unescape(raw.get("incorrect_answers", []))
    if not isinstance(raw_incorrect, list):
        return None
    incorrect = tuple(
        entry.strip()
        for entry in raw_incorrect
        if isinstance(entry, str) and entry.strip()
    )
    category = unescape(raw.get("category", ""))
    return TriviaQuestion(
        category=category.strip() if isinstance(category, str) else "",
        type=question_type,
        question=question.strip(),
        correct_answer=correct.strip(),
        incorrect_answers=incorrect,
    )


def load_categories(config: TriviaConfig) -> dict[str, Category]:
    """Read every ``category_*.json`` file in the configured directory."""
    root = Path(config.directory)
    if not root.is_dir():
        return {}

    categories: dict[str, Category] = {}
    pattern = f"{config.file_prefix}*{config.file_suffix}"
    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        stem = path.stem
        display = (
            stem[len(config.file_prefix) :]
            if stem.startswith(config.file_prefix)
            else stem
        )
        if not display:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        questions = tuple(
            parsed
            for item in data
            if (parsed := parse_question(item)) is not None
        )
        if not questions:
            continue
        categories[normalize_category(display)] = Category(
            name=display, questions=questions
        )
    return categories
