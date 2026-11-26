"""Unit tests for how chat messages are prepared for persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from bot.core.event_router import ChatMessageEvent
from bot.database.repositories.chat_message import _message_row


def make_event(
    *, author_login: str = "chatter", author_name: str = "Chatter", **overrides: object
) -> ChatMessageEvent:
    values: dict[str, object] = {
        "author_id": "42",
        "author_login": author_login,
        "author_name": author_name,
        "channel_id": "7",
        "channel_login": "channel",
        "channel_name": "Channel",
        "content": "hello",
        "message_id": "msg-1",
        "sent_at": None,
    }
    values.update(overrides)
    return ChatMessageEvent(**values)  # type: ignore[arg-type]


def test_username_uses_display_name_capitalization() -> None:
    row = _message_row(make_event(author_login="chatter", author_name="Chatter"))
    assert row is not None
    assert row["username"] == "Chatter"


def test_username_falls_back_to_login_without_display_name() -> None:
    row = _message_row(make_event(author_login="chatter", author_name=""))
    assert row is not None
    assert row["username"] == "chatter"


def test_row_requires_channel_and_author_ids() -> None:
    assert _message_row(make_event(channel_id="")) is None
    assert _message_row(make_event(author_id="")) is None


def test_row_normalizes_timestamp_to_utc() -> None:
    local = datetime(2026, 1, 2, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    row = _message_row(make_event(sent_at=local))
    assert row is not None
    assert row["sent_at"] == datetime(2026, 1, 2, 10, 0, tzinfo=UTC)


def test_row_leaves_timestamp_unset_when_missing() -> None:
    row = _message_row(make_event(sent_at=None))
    assert row is not None
    assert "sent_at" not in row
