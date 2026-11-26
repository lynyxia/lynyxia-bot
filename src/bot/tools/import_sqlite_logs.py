"""Generate a PostgreSQL import script from a legacy SQLite chat log.

The source table must have ``id``, ``datetime``, ``username`` and ``message``
columns. The SQLite ``id`` is ignored; PostgreSQL assigns new IDs. Datetimes
are ``YYYY-MM-DD HH:MM:SS`` in UTC.

The SQLite file is read in two streaming passes and the SQL is written to disk
incrementally, so memory use is independent of the number of messages.

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
import itertools
import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.utils.time import as_utc

SOURCE_COLUMNS = {"id", "datetime", "username", "message"}
PREVIEW_LINES = 14


@dataclass(frozen=True)
class ParsedMessage:
    username: str
    sent_at: datetime
    content: str


@dataclass(frozen=True)
class UserActivity:
    display_name: str
    first: datetime
    last: datetime


@dataclass(frozen=True)
class SourceSummary:
    """Bounded result of the first pass: distinct users, bounds and counts."""

    usernames: list[str]
    activity: dict[str, UserActivity]  # keyed by lower-cased username
    valid: int
    invalid: int


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


def iter_source_rows(path: Path) -> Iterator[tuple[object, object, object]]:
    """Yield (datetime, username, message) lazily; ``id`` is never selected."""
    if not path.exists():
        raise FileNotFoundError(f"SQLite source not found: {path}")

    connection = sqlite3.connect(path)
    try:
        table = find_source_table(connection)
    except BaseException:
        connection.close()
        raise
    quoted = table.replace('"', '""')

    def stream() -> Iterator[tuple[object, object, object]]:
        try:
            # sqlite3 cursors are lazy: each row is fetched only when asked for.
            # Only these three columns are selected; the source id is ignored.
            yield from connection.execute(
                f'SELECT datetime, username, message FROM "{quoted}"'
            )
        finally:
            connection.close()

    return stream()


def parse_row(
    raw_datetime: object, raw_username: object, raw_content: object
) -> ParsedMessage | None:
    """Validate one source row without materializing anything else."""
    sent_at = parse_datetime(raw_datetime)
    username = str(raw_username or "").strip()
    content = str(raw_content or "").strip()
    if sent_at is None or not username or not content:
        return None
    return ParsedMessage(username, sent_at, content)


def read_messages(path: Path) -> Iterator[ParsedMessage]:
    """Yield valid messages one at a time; invalid rows are skipped."""
    source = iter_source_rows(path)

    def messages() -> Iterator[ParsedMessage]:
        for raw_datetime, raw_username, raw_content in source:
            message = parse_row(raw_datetime, raw_username, raw_content)
            if message is not None:
                yield message

    return messages()


def summarize_source(path: Path) -> SourceSummary:
    """First streaming pass: distinct usernames and their time bounds."""
    activity: dict[str, UserActivity] = {}
    valid = 0
    invalid = 0
    for raw_datetime, raw_username, raw_content in iter_source_rows(path):
        message = parse_row(raw_datetime, raw_username, raw_content)
        if message is None:
            invalid += 1
            continue
        valid += 1
        key = message.username.lower()
        entry = activity.get(key)
        if entry is None:
            activity[key] = UserActivity(
                message.username, message.sent_at, message.sent_at
            )
        else:
            activity[key] = UserActivity(
                entry.display_name,
                min(entry.first, message.sent_at),
                max(entry.last, message.sent_at),
            )

    return SourceSummary(
        usernames=sorted(entry.display_name for entry in activity.values()),
        activity=activity,
        valid=valid,
        invalid=invalid,
    )


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


def build_user_activity(
    summary: SourceSummary, user_map: dict[str, str]
) -> dict[str, UserActivity]:
    """Merge per-username activity into one entry per resolved user ID."""
    by_id: dict[str, UserActivity] = {}
    # summary.activity keeps first-occurrence order, so the first display name
    # for a user ID wins, as in the original implementation.
    for key, entry in summary.activity.items():
        user_id = user_map[key]
        current = by_id.get(user_id)
        if current is None:
            by_id[user_id] = entry
        else:
            by_id[user_id] = UserActivity(
                current.display_name,
                min(current.first, entry.first),
                max(current.last, entry.last),
            )
    return by_id


def iter_rows(
    messages: Iterator[ParsedMessage],
    user_map: dict[str, str],
    channel_id: str,
) -> Iterator[dict[str, Any]]:
    """Yield one insert payload per message.

    Duplicates are intentionally not filtered here: every INSERT is guarded by
    ``WHERE NOT EXISTS`` inside the same transaction, so duplicates are skipped
    when the script is applied without holding a set of every message in RAM.
    """
    for message in messages:
        yield {
            "channel_id": channel_id,
            "user_id": user_map[message.username.lower()],
            "username": message.username,
            "content": message.content,
            "sent_at": message.sent_at,
        }


def sql_literal(value: str | None) -> str:
    """Escape a value as a PostgreSQL string literal."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def timestamp_literal(value: datetime) -> str:
    return sql_literal(value.isoformat())


def iter_sql_lines(
    rows: Iterator[dict[str, Any]],
    channel_id: str,
    channel_login: str,
    users: dict[str, UserActivity],
) -> Iterator[str]:
    """Yield the import script one line at a time."""
    yield "-- Generated by bot.tools.import_sqlite_logs."
    yield "-- Review before applying. SQLite IDs are intentionally not imported."
    yield "BEGIN;"
    yield ""
    yield (
        "INSERT INTO channels (id, login, display_name) "
        f"VALUES ({sql_literal(channel_id)}, {sql_literal(channel_login.lower())}, "
        f"{sql_literal(channel_login)}) "
        "ON CONFLICT (id) DO UPDATE SET "
        "login = EXCLUDED.login, display_name = EXCLUDED.display_name;"
    )
    yield ""

    for user_id in sorted(users):
        entry = users[user_id]
        yield (
            "INSERT INTO users (id, login, display_name, first_seen_at, last_seen_at) "
            f"VALUES ({sql_literal(user_id)}, "
            f"{sql_literal(entry.display_name.lower())}, "
            f"{sql_literal(entry.display_name)}, "
            f"{timestamp_literal(entry.first)}, "
            f"{timestamp_literal(entry.last)}) "
            "ON CONFLICT (id) DO UPDATE SET "
            "login = EXCLUDED.login, display_name = EXCLUDED.display_name;"
        )
    yield ""

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
        yield (
            f"INSERT INTO chat_messages ({columns}) "
            f"SELECT {values} "
            f"WHERE NOT EXISTS (SELECT 1 FROM chat_messages WHERE {match});"
        )

    if users:
        user_ids = ", ".join(sql_literal(user_id) for user_id in sorted(users))
        yield ""
        yield "UPDATE users AS u"
        yield "SET first_seen_at = s.first_seen_at, last_seen_at = s.last_seen_at"
        yield "FROM ("
        yield (
            "    SELECT user_id, MIN(sent_at) AS first_seen_at, "
            "MAX(sent_at) AS last_seen_at"
        )
        yield "    FROM chat_messages"
        yield f"    WHERE user_id IN ({user_ids})"
        yield "    GROUP BY user_id"
        yield ") AS s"
        yield "WHERE u.id = s.user_id;"

    yield ""
    yield "COMMIT;"
    yield ""


def write_sql(
    output: Path,
    rows: Iterator[dict[str, Any]],
    channel_id: str,
    channel_login: str,
    users: dict[str, UserActivity],
) -> None:
    """Stream the import script to disk without holding it in memory."""
    with output.open("w", encoding="utf-8") as handle:
        for line in iter_sql_lines(rows, channel_id, channel_login, users):
            handle.write(line + "\n")


async def run(args: argparse.Namespace) -> None:
    source = Path(args.source)
    user_map_path = Path(args.user_map)
    output = Path(args.output)

    summary = summarize_source(source)
    print(
        f"Read {summary.valid} valid message(s) from {source}; "
        f"skipped {summary.invalid}."
    )

    channel_id = await prompt_channel_id(args.channel_id)
    channel_login = args.channel_login or await prompt("Channel login/name: ")

    user_map = load_user_map(user_map_path)
    resolved = await prompt_user_ids(summary.usernames, user_map)
    users = build_user_activity(summary, resolved)

    if args.dry_run:
        print(f"DRY RUN: would write {summary.valid} message(s) to {output}.")
        print("--- SQL preview ---")
        stream = iter_sql_lines(
            iter_rows(read_messages(source), resolved, channel_id),
            channel_id,
            channel_login,
            users,
        )
        preview = list(itertools.islice(stream, PREVIEW_LINES))
        stream.close()
        print("\n".join(preview))
        return

    write_sql(
        output,
        iter_rows(read_messages(source), resolved, channel_id),
        channel_id,
        channel_login,
        users,
    )
    if resolved != user_map:
        save_user_map(user_map_path, resolved)
    print(
        f"Wrote {output}: {summary.valid} message(s), {len(users)} user(s). "
        "Apply it yourself with: psql \"$DATABASE_URL\" -f "
        f"{output.as_posix()}"
    )


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
