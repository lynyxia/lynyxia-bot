"""Persistence and quote queries for chat messages."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.core.event_router import ChatMessageEvent
from bot.database.models import Channel, ChatMessage, User
from bot.database.repositories.base import BaseRepository
from bot.utils.time import as_utc


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
                if not event.channel_id or not event.author_id:
                    continue
                row: dict[str, object] = {
                    "channel_id": event.channel_id,
                    "user_id": event.author_id,
                    "username": event.author_login or event.author_name,
                    "content": event.content,
                    "twitch_message_id": event.message_id,
                }
                if event.sent_at is not None:
                    row["sent_at"] = as_utc(event.sent_at)
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
