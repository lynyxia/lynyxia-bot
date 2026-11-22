"""Generate a PostgreSQL import script from a legacy SQLite chat log.

The source table must have ``id``, ``datetime``, ``username`` and ``message``
columns. The SQLite ``id`` is ignored; PostgreSQL assigns new IDs. Datetimes
are ``YYYY-MM-DD HH:MM:SS`` in UTC.

Run with:
    python -m bot.tools.import_sqlite_logs --source logs.db --dry-run
    python -m bot.tools.import_sqlite_logs --source logs.db \
        --output legacy_import.sql

The generated file is plain SQL that you can review and apply yourself, e.g.:
    psql "$DATABASE_URL" -f legacy_import.sql
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.utils.time import as_utc

SOURCE_COLUMNS = {"id", "datetime", "username", "message"}


@dataclass(frozen=True)
class ParsedMessage:
    username: str
    sent_at: datetime
    content: str


def find_source_table(connection: sqlite3.Connection) -> str:
    """Find the SQLite table containing all four expected columns."""
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    ]
    for table in tables:
        quoted = table.replace('"', '""')
        columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")')
        }
        if SOURCE_COLUMNS <= columns:
            return table
    raise ValueError(
        f"No table with columns {sorted(SOURCE_COLUMNS)} found in the SQLite file"
    )


def parse_datetime(value: object) -> datetime | None:
    """Parse a ``YYYY-MM-DD HH:MM:SS`` UTC timestamp."""
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return as_utc(parsed)


def read_messages(path: Path) -> tuple[list[ParsedMessage], int]:
    """Return valid messages and the number of skipped/invalid rows."""
    if not path.exists():
        raise FileNotFoundError(f"SQLite source not found: {path}")

    connection = sqlite3.connect(path)
    try:
        table = find_source_table(connection)
        quoted = table.replace('"', '""')
        rows = connection.execute(
            f'SELECT id, datetime, username, message FROM "{quoted}"'
        ).fetchall()
    finally:
        connection.close()

    messages: list[ParsedMessage] = []
    invalid = 0
    for _source_id, raw_datetime, raw_username, raw_content in rows:
        sent_at = parse_datetime(raw_datetime)
        username = str(raw_username or "").strip()
        content = str(raw_content or "").strip()
        if sent_at is None or not username or not content:
            invalid += 1
            continue
        messages.append(ParsedMessage(username, sent_at, content))
    return messages, invalid


def load_user_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"User map must be a JSON object: {path}")
    return {str(key).strip().lower(): str(value) for key, value in data.items()}


def save_user_map(path: Path, user_map: dict[str, str]) -> None:
    ordered = {key: user_map[key] for key in sorted(user_map)}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def is_valid_user_id(value: str) -> bool:
    return value.isdigit() and 0 < len(value) <= 32 and int(value) > 0


async def prompt(message: str) -> str:
    """Prompt from a thread so the CLI never blocks its own event loop."""
    return (await asyncio.to_thread(input, message)).strip()


async def prompt_channel_id(current: str | None) -> str:
    while True:
        value = current or await prompt("Channel ID (numeric Twitch user ID): ")
        if value.isdigit():
            return value
        print("Channel ID must contain digits only.")
        current = None


async def prompt_user_ids(
    usernames: Sequence[str], user_map: dict[str, str]
) -> dict[str, str]:
    """Ask for a user ID once per distinct new username."""
    resolved = dict(user_map)
    for username in usernames:
        key = username.lower()
        if key in resolved:
            continue
        while True:
            value = await prompt(f"Twitch user ID for '{username}': ")
            if is_valid_user_id(value):
                resolved[key] = value
                break
            print("User ID must be 1-32 digits (a Twitch user ID).")
    return resolved


def message_key(message: ParsedMessage) -> tuple[datetime, str, str]:
    return (message.sent_at, message.username.lower(), message.content)


def build_rows(
    messages: Sequence[ParsedMessage],
    user_map: dict[str, str],
    channel_id: str,
    existing: set[tuple[datetime, str, str]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Build insert payloads, skipping duplicates within the source data."""
    seen = set(existing or ())
    rows: list[dict[str, Any]] = []
    duplicates = 0
    for message in messages:
        key = message_key(message)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        rows.append(
            {
                "channel_id": channel_id,
                "user_id": user_map[message.username.lower()],
                "username": message.username,
                "content": message.content,
                "sent_at": message.sent_at,
            }
        )
    return rows, duplicates


def sql_literal(value: str | None) -> str:
    """Escape a value as a PostgreSQL string literal."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def timestamp_literal(value: datetime) -> str:
    return sql_literal(value.isoformat())


def render_sql(
    rows: Sequence[dict[str, Any]], channel_id: str, channel_login: str
) -> str:
    """Render a complete, idempotent PostgreSQL import script."""
    lines = [
        "-- Generated by bot.tools.import_sqlite_logs.",
        "-- Review before applying. SQLite IDs are intentionally not imported.",
        "BEGIN;",
        "",
        "INSERT INTO channels (id, login, display_name) "
        f"VALUES ({sql_literal(channel_id)}, {sql_literal(channel_login.lower())}, "
        f"{sql_literal(channel_login)}) "
        "ON CONFLICT (id) DO UPDATE SET "
        "login = EXCLUDED.login, display_name = EXCLUDED.display_name;",
        "",
    ]

    by_user: dict[str, dict[str, Any]] = {}
    for row in rows:
        user_id = row["user_id"]
        entry = by_user.get(user_id)
        if entry is None:
            entry = {
                "login": row["username"].lower(),
                "display_name": row["username"],
                "first": row["sent_at"],
                "last": row["sent_at"],
            }
            by_user[user_id] = entry
        else:
            entry["first"] = min(entry["first"], row["sent_at"])
            entry["last"] = max(entry["last"], row["sent_at"])

    for user_id in sorted(by_user):
        entry = by_user[user_id]
        lines.append(
            "INSERT INTO users (id, login, display_name, first_seen_at, last_seen_at) "
            f"VALUES ({sql_literal(user_id)}, {sql_literal(entry['login'])}, "
            f"{sql_literal(entry['display_name'])}, "
            f"{timestamp_literal(entry['first'])}, "
            f"{timestamp_literal(entry['last'])}) "
            "ON CONFLICT (id) DO UPDATE SET "
            "login = EXCLUDED.login, display_name = EXCLUDED.display_name;"
        )
    lines.append("")

    columns = "channel_id, user_id, username, content, sent_at"
    for row in rows:
        values = ", ".join(
            [
                sql_literal(row["channel_id"]),
                sql_literal(row["user_id"]),
                sql_literal(row["username"]),
                sql_literal(row["content"]),
                timestamp_literal(row["sent_at"]),
            ]
        )
        match = " AND ".join(
            [
                f"channel_id = {sql_literal(row['channel_id'])}",
                f"user_id = {sql_literal(row['user_id'])}",
                f"username = {sql_literal(row['username'])}",
                f"content = {sql_literal(row['content'])}",
                f"sent_at = {timestamp_literal(row['sent_at'])}",
            ]
        )
        lines.append(
            f"INSERT INTO chat_messages ({columns}) "
            f"SELECT {values} "
            f"WHERE NOT EXISTS (SELECT 1 FROM chat_messages WHERE {match});"
        )

    if by_user:
        user_ids = ", ".join(sql_literal(user_id) for user_id in sorted(by_user))
        lines.extend(
            [
                "",
                "UPDATE users AS u",
                "SET first_seen_at = s.first_seen_at, last_seen_at = s.last_seen_at",
                "FROM (",
                "    SELECT user_id, MIN(sent_at) AS first_seen_at, "
                "MAX(sent_at) AS last_seen_at",
                "    FROM chat_messages",
                f"    WHERE user_id IN ({user_ids})",
                "    GROUP BY user_id",
                ") AS s",
                "WHERE u.id = s.user_id;",
            ]
        )

    lines.extend(["", "COMMIT;", ""])
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    source = Path(args.source)
    user_map_path = Path(args.user_map)
    output = Path(args.output)

    messages, invalid = read_messages(source)
    usernames = sorted({message.username for message in messages})
    print(f"Read {len(messages)} valid message(s) from {source}; skipped {invalid}.")

    channel_id = await prompt_channel_id(args.channel_id)
    channel_login = args.channel_login or await prompt("Channel login/name: ")

    user_map = load_user_map(user_map_path)
    resolved = await prompt_user_ids(usernames, user_map)

    rows, duplicates = build_rows(messages, resolved, channel_id)
    sql = render_sql(rows, channel_id, channel_login)

    if args.dry_run:
        print(f"DRY RUN: would write {len(rows)} message(s) to {output}.")
        print(f"Duplicates skipped from source: {duplicates}.")
        print("--- SQL preview ---")
        print("\n".join(sql.splitlines()[:14]))
        return

    output.write_text(sql, encoding="utf-8")
    if resolved != user_map:
        save_user_map(user_map_path, resolved)
    print(
        f"Wrote {output}: {len(rows)} message(s), {len(by_user_ids(rows))} user(s); "
        f"skipped {duplicates} duplicate(s) from the source. "
        "Apply it yourself with: psql \"$DATABASE_URL\" -f "
        f"{output.as_posix()}"
    )


def by_user_ids(rows: Sequence[dict[str, Any]]) -> set[str]:
    return {row["user_id"] for row in rows}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="logs.db", help="SQLite database path")
    parser.add_argument(
        "--user-map", default="user_map.json", help="JSON cache of username -> user ID"
    )
    parser.add_argument(
        "--output", default="legacy_import.sql", help="Generated SQL file path"
    )
    parser.add_argument("--channel-id", help="Numeric Twitch channel ID")
    parser.add_argument("--channel-login", help="Channel login/name")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview SQL without writing files"
    )
    return parser.parse_args(argv)


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
