"""Persistence and quote queries for chat messages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.core.event_router import ChatMessageEvent
from bot.database.models import Channel, ChatMessage, User
from bot.database.repositories.base import BaseRepository
from bot.utils.time import as_utc


@dataclass(frozen=True)
class Chatter:
    """An active chatter in one channel, for the AI chat discovery tool."""

    user_id: str
    login: str
    display_name: str
    message_count: int
    last_seen_at: datetime


@dataclass(frozen=True)
class ChatSample:
    """A message returned by the AI chat sampling and search tools."""

    user_id: str
    username: str
    content: str
    sent_at: datetime


def _message_row(event: ChatMessageEvent) -> dict[str, object] | None:
    """Build one insert row; return ``None`` for events lacking required IDs."""
    if not event.channel_id or not event.author_id:
        return None
    # Store the chatter's display-name capitalization, matching users.display_name
    # (which is populated from the same event). Fall back to the login only when
    # Twitch did not supply a display name.
    row: dict[str, object] = {
        "channel_id": event.channel_id,
        "user_id": event.author_id,
        "username": event.author_name or event.author_login,
        "content": event.content,
        "twitch_message_id": event.message_id,
    }
    if event.sent_at is not None:
        row["sent_at"] = as_utc(event.sent_at)
    return row


class ChatMessageRepository(BaseRepository):
    async def add(
        self,
        *,
        channel_id: str,
        user_id: str,
        content: str,
        username: str = "",
        sent_at: datetime | None = None,
        twitch_message_id: str | None = None,
    ) -> ChatMessage:
        fields: dict[str, object] = {
            "channel_id": channel_id,
            "user_id": user_id,
            "username": username,
            "content": content,
            "twitch_message_id": twitch_message_id,
        }
        if sent_at is not None:
            fields["sent_at"] = as_utc(sent_at)
        message = ChatMessage(**fields)  # type: ignore[arg-type]
        async with self.database.session() as session:
            session.add(message)
            await session.commit()
            await session.refresh(message)
        return message

    async def add_many(self, messages: Sequence[ChatMessage]) -> None:
        async with self.database.session() as session:
            session.add_all(messages)
            await session.commit()

    async def record_batch(self, events: Sequence[ChatMessageEvent]) -> int:
        """Persist a batch of messages with their channel/user rows in one transaction.

        Returns the number of newly inserted messages. Duplicate Twitch message
        IDs (e.g. redelivered events) are ignored.
        """
        if not events:
            return 0

        channels = {
            event.channel_id: (event.channel_login, event.channel_name)
            for event in events
            if event.channel_id
        }
        users = {
            event.author_id: (event.author_login, event.author_name)
            for event in events
            if event.author_id
        }

        async with self.database.session() as session:
            if channels:
                stmt = pg_insert(Channel).values(
                    [
                        {"id": channel_id, "login": login, "display_name": name}
                        for channel_id, (login, name) in channels.items()
                    ]
                )
                excluded = stmt.excluded
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "login": excluded.login,
                            "display_name": excluded.display_name,
                        },
                    )
                )

            if users:
                stmt = pg_insert(User).values(
                    [
                        {"id": user_id, "login": login, "display_name": name}
                        for user_id, (login, name) in users.items()
                    ]
                )
                excluded = stmt.excluded
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "login": excluded.login,
                            "display_name": excluded.display_name,
                            "last_seen_at": func.now(),
                        },
                    )
                )

            rows: list[dict[str, object]] = []
            for event in events:
                row = _message_row(event)
                if row is not None:
                    rows.append(row)

            if not rows:
                await session.commit()
                return 0

            result = await session.execute(
                pg_insert(ChatMessage)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["twitch_message_id"])
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def delete_before(
        self, cutoff: datetime, channel_id: str | None = None
    ) -> int:
        """Delete messages older than ``cutoff`` (retention extension point)."""
        stmt = delete(ChatMessage).where(ChatMessage.sent_at < cutoff)
        if channel_id is not None:
            stmt = stmt.where(ChatMessage.channel_id == channel_id)
        async with self.database.session() as session:
            result = await session.execute(stmt)
            await session.commit()
        return int(result.rowcount or 0)

    async def get_random(
        self,
        channel_id: str,
        excluded_user_ids: Sequence[str] = (),
    ) -> ChatMessage | None:
        stmt = select(ChatMessage).where(ChatMessage.channel_id == channel_id)
        if excluded_user_ids:
            stmt = stmt.where(ChatMessage.user_id.not_in(tuple(excluded_user_ids)))
        stmt = stmt.order_by(func.random()).limit(1)
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_recent_by_user(
        self, channel_id: str, user_id: str, limit: int
    ) -> Sequence[ChatMessage]:
        """Recent messages from one user in one channel, newest first."""
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.channel_id == channel_id,
                ChatMessage.user_id == user_id,
            )
            .order_by(ChatMessage.sent_at.desc())
            .limit(limit)
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_random_by_user_id(
        self, channel_id: str, user_id: str
    ) -> ChatMessage | None:
        """Find a random message from a stable Twitch user ID."""
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.channel_id == channel_id,
                ChatMessage.user_id == user_id,
            )
            .order_by(func.random())
            .limit(1)
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_random_by_user(
        self, channel_id: str, user_login: str
    ) -> ChatMessage | None:
        """Deprecated: resolve logins via ``UserRepository`` and use IDs instead."""
        stmt = (
            select(ChatMessage)
            .join(User, ChatMessage.user_id == User.id)
            .where(
                ChatMessage.channel_id == channel_id,
                func.lower(User.login) == user_login.lower(),
            )
            .order_by(func.random())
            .limit(1)
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def count_and_first(
        self, channel_id: str, user_id: str
    ) -> tuple[int, datetime | None]:
        """Return a user's message count and their earliest timestamp in a channel."""
        stmt = select(
            func.count(), func.min(ChatMessage.sent_at)
        ).where(
            ChatMessage.channel_id == channel_id,
            ChatMessage.user_id == user_id,
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            count, first_at = result.one()
        return int(count), first_at

    async def count_emote(
        self,
        channel_id: str,
        pattern: str,
        exclude_pattern: str,
        since: datetime,
    ) -> int:
        """Count messages in ``channel_id`` matching ``pattern`` since ``since``.

        ``exclude_pattern`` removes command invocations (e.g. ``!ecount Kappa``)
        so asking for a count does not inflate it. Both patterns are supplied by
        the caller and passed to PostgreSQL as bound parameters.
        """
        stmt = (
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.channel_id == channel_id,
                ChatMessage.sent_at >= since,
                ChatMessage.content.op("~")(pattern),
                ChatMessage.content.op("!~")(exclude_pattern),
            )
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def list_chatters(
        self,
        channel_id: str,
        *,
        since: datetime | None = None,
        min_messages: int = 1,
        limit: int = 20,
        exclude_user_ids: Sequence[str] = (),
    ) -> Sequence[Chatter]:
        """Active chatters in one channel, most messages first.

        Powers the AI chat discovery tool: unlike ``get_recent_by_user`` this
        does not require knowing a username in advance.
        """
        message_count = func.count(ChatMessage.id)
        stmt = (
            select(
                User.id,
                User.login,
                User.display_name,
                message_count.label("message_count"),
                func.max(ChatMessage.sent_at).label("last_seen_at"),
            )
            .join(User, ChatMessage.user_id == User.id)
            .where(ChatMessage.channel_id == channel_id)
        )
        if since is not None:
            stmt = stmt.where(ChatMessage.sent_at >= since)
        if exclude_user_ids:
            stmt = stmt.where(ChatMessage.user_id.not_in(tuple(exclude_user_ids)))
        stmt = (
            stmt.group_by(User.id, User.login, User.display_name)
            .having(message_count >= min_messages)
            .order_by(message_count.desc(), User.login)
            .limit(limit)
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return [
                Chatter(
                    user_id=row.id,
                    login=row.login,
                    display_name=row.display_name,
                    message_count=int(row.message_count),
                    last_seen_at=row.last_seen_at,
                )
                for row in result
            ]

    async def get_samples_by_users(
        self,
        channel_id: str,
        user_ids: Sequence[str],
        *,
        per_user: int = 3,
        random: bool = False,
        since: datetime | None = None,
    ) -> Sequence[ChatSample]:
        """A few messages per user in one query, newest or random per user.

        A window function keeps this to a single round trip even when the AI
        chat tool samples dozens of users in one call.
        """
        if not user_ids or per_user <= 0:
            return ()
        ordering = func.random() if random else ChatMessage.sent_at.desc()
        ranked = (
            select(
                ChatMessage.user_id,
                ChatMessage.username,
                ChatMessage.content,
                ChatMessage.sent_at,
                func.row_number()
                .over(partition_by=ChatMessage.user_id, order_by=ordering)
                .label("rn"),
            )
            .where(
                ChatMessage.channel_id == channel_id,
                ChatMessage.user_id.in_(tuple(user_ids)),
            )
        )
        if since is not None:
            ranked = ranked.where(ChatMessage.sent_at >= since)
        sub = ranked.subquery("ranked")
        stmt = (
            select(sub.c.user_id, sub.c.username, sub.c.content, sub.c.sent_at)
            .where(sub.c.rn <= per_user)
            .order_by(sub.c.user_id, sub.c.sent_at.desc())
        )
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return [
                ChatSample(
                    user_id=row.user_id,
                    username=row.username,
                    content=row.content,
                    sent_at=row.sent_at,
                )
                for row in result
            ]

    async def search(
        self,
        channel_id: str,
        term: str,
        *,
        since: datetime | None = None,
        limit: int = 20,
        user_id: str | None = None,
        exclude_user_ids: Sequence[str] = (),
    ) -> Sequence[ChatSample]:
        """Find messages containing ``term`` in one channel, newest first.

        Wildcards in the user-supplied term are escaped and the whole pattern
        is bound as a parameter, never interpolated into SQL.
        """
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = select(ChatMessage).where(
            ChatMessage.channel_id == channel_id,
            ChatMessage.content.ilike(f"%{escaped}%", escape="\\"),
        )
        if since is not None:
            stmt = stmt.where(ChatMessage.sent_at >= since)
        if user_id is not None:
            stmt = stmt.where(ChatMessage.user_id == user_id)
        if exclude_user_ids:
            stmt = stmt.where(ChatMessage.user_id.not_in(tuple(exclude_user_ids)))
        stmt = stmt.order_by(ChatMessage.sent_at.desc()).limit(limit)
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return [
                ChatSample(
                    user_id=row.user_id,
                    username=row.username,
                    content=row.content,
                    sent_at=row.sent_at,
                )
                for row in result
            ]
