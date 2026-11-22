"""Database integration tests.

Set TEST_DATABASE_URL to a disposable PostgreSQL database to run these, e.g.:

    TEST_DATABASE_URL=postgresql+asyncpg://bot:pw@localhost:5432/test \
        pytest tests/test_database.py

They are skipped otherwise so the default suite needs no running database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from bot.config import DatabaseSettings
from bot.core.event_router import ChatMessageEvent
from bot.database.models import Base, Channel, User
from bot.database.repositories import (
    AiChatAllowlistRepository,
    AiChatChannelRepository,
    AiChatThreadRepository,
    ChannelAuthorizationRepository,
    ChatMessageRepository,
    OAuthTokenRepository,
    UserRepository,
)
from bot.services.database import DatabaseService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured"
)


async def test_repository_roundtrip() -> None:
    config = DatabaseSettings(
        url=TEST_DATABASE_URL,
        password=SecretStr("unused"),
        pool_size=1,
        max_overflow=0,
    )
    database = DatabaseService(config)
    await database.connect()

    assert database._engine is not None
    async with database._engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with database.session() as session:
            session.add_all(
                [
                    Channel(id="7", login="streamer", display_name="Streamer"),
                    User(id="42", login="chatter", display_name="Chatter"),
                    User(id="43", login="other", display_name="Other"),
                ]
            )
            await session.commit()

        repository = ChatMessageRepository(database)
        message = await repository.add(
            channel_id="7", user_id="42", username="chatter", content="hello world"
        )
        assert message.id is not None
        assert message.sent_at is not None
        assert message.sent_at.tzinfo is not None
        assert message.username == "chatter"

        await repository.add(channel_id="7", user_id="43", content="another quote")

        random_quote = await repository.get_random("7")
        assert random_quote is not None
        assert random_quote.content in {"hello world", "another quote"}

        user_quote = await repository.get_random_by_user("7", "CHATTER")
        assert user_quote is not None
        assert user_quote.content == "hello world"

        users = UserRepository(database)
        chatter = await users.get_by_login("CHATTER")
        assert chatter is not None
        assert chatter.id == "42"
        assert await users.get_by_login("nobody") is None

        by_id = await repository.get_random_by_user_id("7", "42")
        assert by_id is not None
        assert by_id.content == "hello world"
        assert await repository.get_random("7", ("42", "43")) is None

        now = datetime.now(UTC)
        events = [
            ChatMessageEvent(
                author_id="44",
                author_login="batchuser",
                author_name="Batch User",
                channel_id="8",
                channel_login="otherchannel",
                channel_name="Other Channel",
                content=f"batch {index}",
                message_id=f"batch-{index}",
                sent_at=now,
            )
            for index in range(3)
        ]
        events.append(events[0])  # redelivered event must be ignored
        inserted = await repository.record_batch(events)
        assert inserted == 3
        batch_message = await repository.get_random("8")
        assert batch_message is not None
        assert batch_message.username == "batchuser"
        assert batch_message.sent_at is not None
        assert batch_message.sent_at.utcoffset() == timedelta(0)

        deleted = await repository.delete_before(now + timedelta(days=1), "8")
        assert deleted == 3
        assert await repository.get_random("8") is None

        tokens = OAuthTokenRepository(database)
        assert await tokens.get("42") is None
        await tokens.save("42", "access-1", "refresh-1", "user:read:chat")
        stored = await tokens.get("42")
        assert stored is not None
        assert stored.access_token == "access-1"
        await tokens.save("42", "access-2", "refresh-2", "user:read:chat")
        rotated = await tokens.get("42")
        assert rotated is not None
        assert rotated.access_token == "access-2"
        await tokens.delete("42")
        assert await tokens.get("42") is None

        channels = ChannelAuthorizationRepository(database)
        assert await channels.get("7") is None
        await channels.save("7", "streamer", "access", "refresh", "channel:bot")
        stored_channel = await channels.get("7")
        assert stored_channel is not None
        assert stored_channel.broadcaster_login == "streamer"
        assert len(await channels.list_all()) == 1
        await channels.delete("7")
        assert await channels.get("7") is None

        ai_channels = AiChatChannelRepository(database)
        assert await ai_channels.get("7") is None
        await ai_channels.set_persona("7", "generic.md")
        channel_settings = await ai_channels.get("7")
        assert channel_settings is not None
        assert channel_settings.persona == "generic.md"

        allowlist = AiChatAllowlistRepository(database)
        await allowlist.add("7", "42", "chatter")
        assert await allowlist.contains("7", "42")
        assert len(await allowlist.list("7")) == 1
        assert await allowlist.remove("7", "42")

        threads = AiChatThreadRepository(database)
        await threads.save(
            "thread-1", "7", "42", "generic.md", [{"role": "user", "content": "hi"}]
        )
        thread = await threads.get("thread-1")
        assert thread is not None
        assert thread.messages == [{"role": "user", "content": "hi"}]
    finally:
        async with database._engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()
