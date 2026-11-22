"""Tests for the standalone SQLite -> PostgreSQL import tool."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime

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
    messages, invalid = tool.read_messages(path)
    assert len(messages) == 1
    assert invalid == 3
    assert messages[0].username == "Kappa123"


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


def test_build_rows_dedupes_within_and_against_existing() -> None:
    first = tool.ParsedMessage("Alice", datetime(2021, 1, 1, tzinfo=UTC), "hello")
    second = tool.ParsedMessage("Bob", datetime(2021, 1, 2, tzinfo=UTC), "world")
    user_map = {"alice": "1", "bob": "2"}

    rows, duplicates = tool.build_rows([first, first, second], user_map, "7")
    assert len(rows) == 2
    assert duplicates == 1

    existing = {(first.sent_at, "alice", "hello")}
    rows, duplicates = tool.build_rows([first, second], user_map, "7", existing)
    assert len(rows) == 1
    assert duplicates == 1


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
