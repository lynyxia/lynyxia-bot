"""Tests for the standalone SQLite -> PostgreSQL import tool."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bot.tools import import_sqlite_logs as tool


def make_sqlite(path, rows: list[tuple], table: str = "messages") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            f"CREATE TABLE {table} "
            "(id INTEGER, datetime TEXT, username TEXT, message TEXT)"
        )
        connection.executemany(
            f"INSERT INTO {table} (id, datetime, username, message) VALUES (?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def test_find_source_table(tmp_path) -> None:
    path = tmp_path / "logs.db"
    make_sqlite(path, [], table="chat_log")
    connection = sqlite3.connect(path)
    try:
        assert tool.find_source_table(connection) == "chat_log"
    finally:
        connection.close()


def test_parse_datetime_is_utc() -> None:
    parsed = tool.parse_datetime("2021-08-11 17:25:54")
    assert parsed is not None
    assert parsed.tzinfo is UTC
    assert parsed.hour == 17
    assert tool.parse_datetime("not a date") is None


def test_read_messages_skips_invalid_rows(tmp_path) -> None:
    path = tmp_path / "logs.db"
    make_sqlite(
        path,
        [
            (1, "2021-08-11 17:25:54", "Kappa123", "hello"),
            (2, "bad timestamp", "Someone", "ignored"),
            (3, "2021-08-11 17:26:00", "", "ignored"),
            (4, "2021-08-11 17:26:01", "Other", ""),
        ],
    )
    messages = list(tool.read_messages(path))
    summary = tool.summarize_source(path)
    assert len(messages) == 1
    assert summary.invalid == 3
    assert summary.valid == 1
    assert messages[0].username == "Kappa123"


def test_read_messages_is_lazy(tmp_path) -> None:
    path = tmp_path / "logs.db"
    make_sqlite(path, [(1, "2021-08-11 17:25:54", "Kappa123", "hello")])
    messages = tool.read_messages(path)
    assert isinstance(messages, Iterator)
    assert next(iter(messages)).content == "hello"


def test_summarize_source_tracks_time_bounds_per_username(tmp_path) -> None:
    path = tmp_path / "logs.db"
    make_sqlite(
        path,
        [
            (1, "2021-08-11 17:25:54", "Alice", "first"),
            (2, "2021-08-12 17:25:54", "alice", "second"),
            (3, "2021-08-10 17:25:54", "Bob", "third"),
        ],
    )
    summary = tool.summarize_source(path)
    assert summary.usernames == ["Alice", "Bob"]
    assert summary.activity["alice"].display_name == "Alice"
    assert summary.activity["alice"].first.day == 11
    assert summary.activity["alice"].last.day == 12
    assert summary.activity["bob"].first.day == 10
    assert summary.activity["bob"].last.day == 10

    users = tool.build_user_activity(summary, {"alice": "1", "bob": "1"})
    assert len(users) == 1
    entry = users["1"]
    assert entry.display_name == "Alice"
    assert entry.first.day == 10
    assert entry.last.day == 12


def test_user_map_roundtrip(tmp_path) -> None:
    path = tmp_path / "user_map.json"
    assert tool.load_user_map(path) == {}
    tool.save_user_map(path, {"Kappa123": "42"})
    assert tool.load_user_map(path) == {"kappa123": "42"}


async def test_prompt_user_ids_asks_once_per_username(monkeypatch) -> None:
    answers = iter(["111", "bad-id", "222"])

    async def fake_prompt(_text: str) -> str:
        return next(answers)

    monkeypatch.setattr(tool, "prompt", fake_prompt)
    resolved = await tool.prompt_user_ids(["Alice", "alice", "Bob"], {})

    assert resolved == {"alice": "111", "bob": "222"}


def test_iter_rows_yields_one_payload_per_message() -> None:
    first = tool.ParsedMessage("Alice", datetime(2021, 1, 1, tzinfo=UTC), "hello")
    second = tool.ParsedMessage("Bob", datetime(2021, 1, 2, tzinfo=UTC), "world")
    user_map = {"alice": "1", "bob": "2"}

    rows = list(tool.iter_rows(iter([first, first, second]), user_map, "7"))
    # Intra-source duplicates are left to the SQL WHERE NOT EXISTS guard.
    assert len(rows) == 3
    assert rows[0] == {
        "channel_id": "7",
        "user_id": "1",
        "username": "Alice",
        "content": "hello",
        "sent_at": first.sent_at,
    }
    assert rows[2]["user_id"] == "2"


def test_write_sql_streams_partial_output_on_failure(tmp_path) -> None:
    output: Path = tmp_path / "partial.sql"
    sent_at = datetime(2021, 1, 1, tzinfo=UTC)
    row = {
        "channel_id": "7",
        "user_id": "42",
        "username": "Alice",
        "content": "hello",
        "sent_at": sent_at,
    }

    def rows() -> Iterator[dict]:
        yield row
        yield row
        raise RuntimeError("source failed mid-stream")

    users = {"42": tool.UserActivity("Alice", sent_at, sent_at)}
    with pytest.raises(RuntimeError, match="mid-stream"):
        tool.write_sql(output, rows(), "7", "channel", users)

    # Earlier lines reach disk before the failure; no all-at-once string join.
    text = output.read_text(encoding="utf-8")
    assert "BEGIN;" in text
    assert text.count("INSERT INTO chat_messages") == 2
    assert "COMMIT;" not in text


async def test_dry_run_does_not_write(tmp_path, capsys) -> None:
    source = tmp_path / "logs.db"
    make_sqlite(source, [(1, "2021-08-11 17:25:54", "Kappa123", "nice socks")])
    user_map = tmp_path / "user_map.json"
    user_map.write_text(json.dumps({"kappa123": "42"}), encoding="utf-8")
    output_path = tmp_path / "legacy_import.sql"

    args = argparse.Namespace(
        source=str(source),
        user_map=str(user_map),
        output=str(output_path),
        channel_id="7",
        channel_login="channel",
        dry_run=True,
    )
    await tool.run(args)

    printed = capsys.readouterr().out
    assert "DRY RUN: would write 1 message(s)" in printed
    assert "INSERT INTO chat_messages" in printed
    assert not output_path.exists()


async def test_run_writes_sql_file(tmp_path, capsys) -> None:
    source = tmp_path / "logs.db"
    make_sqlite(
        source,
        [
            (1, "2021-08-11 17:25:54", "Kappa123", "nice socks"),
            (2, "2021-08-12 10:00:00", "Kappa123", "second"),
        ],
    )
    user_map = tmp_path / "user_map.json"
    user_map.write_text(json.dumps({"kappa123": "42"}), encoding="utf-8")
    output_path = tmp_path / "legacy_import.sql"

    args = argparse.Namespace(
        source=str(source),
        user_map=str(user_map),
        output=str(output_path),
        channel_id="7",
        channel_login="channel",
        dry_run=False,
    )
    await tool.run(args)

    sql = output_path.read_text(encoding="utf-8")
    assert "BEGIN;" in sql and "COMMIT;" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "WHERE NOT EXISTS" in sql
    assert "UPDATE users AS u" in sql
    assert "'nice socks'" in sql
    assert "2021-08-11T17:25:54+00:00" in sql
    assert "Wrote" in capsys.readouterr().out


def test_sql_literal_escapes_quotes() -> None:
    assert tool.sql_literal("O'Brien") == "'O''Brien'"
    assert tool.sql_literal(None) == "NULL"


@pytest.mark.parametrize("value", ["abc", "0", "x" * 33])
def test_invalid_user_ids(value: str) -> None:
    assert not tool.is_valid_user_id(value)
