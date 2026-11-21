"""Database integration tests.

Set TEST_DATABASE_URL to a disposable PostgreSQL database to run these, e.g.:

    TEST_DATABASE_URL=postgresql+asyncpg://bot:pw@localhost:5432/test \
        pytest tests/test_database.py

They are skipped otherwise so the default suite needs no running database.
"""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from bot.config import DatabaseSettings
from bot.database.models import Base, Channel, User
from bot.database.repositories import ChatMessageRepository
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
            channel_id="7", user_id="42", content="hello world"
        )
        assert message.id is not None
        assert message.sent_at is not None

        await repository.add(channel_id="7", user_id="43", content="another quote")

        random_quote = await repository.get_random("7")
        assert random_quote is not None
        assert random_quote.content in {"hello world", "another quote"}

        user_quote = await repository.get_random_by_user("7", "CHATTER")
        assert user_quote is not None
        assert user_quote.content == "hello world"
    finally:
        async with database._engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()
