"""Loading of persona system prompts from ``*.md`` files."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

MAX_PERSONA_CHARS = 20_000


def is_valid_persona_name(name: str) -> bool:
    """Accept only plain ``*.md`` filenames, never paths."""
    if not name or Path(name).name != name:
        return False
    if not name.endswith(".md") or name in {".md", "..md"}:
        return False
    return all(char not in name for char in ("/", "\\", "\x00"))


def load_personas(directory: str | Path) -> dict[str, str]:
    """Read every persona markdown file in ``directory``."""
    root = Path(directory)
    if not root.is_dir():
        return {}

    personas: dict[str, str] = {}
    for path in sorted(root.glob("*.md")):
        if not is_valid_persona_name(path.name):
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content or len(content) > MAX_PERSONA_CHARS:
            continue
        personas[path.name] = content
    return personas


def format_system_prompt(
    template: str,
    *,
    username: str,
    user_id: str,
    now: datetime | None = None,
) -> str:
    """Replace known placeholders in a persona without touching other braces."""
    current = now or datetime.now(UTC)
    values = {
        "date": current.strftime("%Y-%m-%d"),
        "time": current.strftime("%H:%M:%S"),
        "username": username,
        "user_id": user_id,
    }
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result
