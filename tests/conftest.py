from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from bot.config import DatabaseSettings, Settings, TwitchSettings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        twitch=TwitchSettings(
            client_id="test-client-id",
            client_secret=SecretStr("test-client-secret"),
            bot_username="testbot",
            bot_id="42",
            channel=["testchannel"],
            channel_id=["7"],
            oauth_redirect_uri="http://localhost:8080/oauth/callback",
            oauth_bind_host="127.0.0.1",
            oauth_port=8080,
            oauth_scopes=["user:read:chat", "user:write:chat"],
            channel_scopes=["channel:bot"],
        ),
        database=DatabaseSettings(
            host="localhost",
            password=SecretStr("test-password"),
            name="test_twitch_bot",
            connect_retries=1,
        ),
        enabled_modules=[
            "chat_logger",
            "quotes",
            "ecount",
            "lines",
            "ai_chat",
            "lynyxia",
            "livepass",
            "trivia",
        ],
        admin_user_ids=["42"],
    )


class FakeDatabase:
    def __init__(self) -> None:
        self.connected = False
        self.disposed = False

    async def connect(self) -> None:
        self.connected = True

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def fake_database() -> FakeDatabase:
    return FakeDatabase()


class FakeTokenStore:
    def __init__(self) -> None:
        self.record = None
        self.saved: list[tuple[str, str, str, str]] = []

    async def get(self, user_id: str):
        return self.record

    async def save(
        self, user_id: str, access_token: str, refresh_token: str, scopes: str = ""
    ) -> None:
        self.saved.append((user_id, access_token, refresh_token, scopes))
        self.record = type("Record", (), {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scopes": scopes,
        })()


@pytest.fixture
def fake_token_store() -> FakeTokenStore:
    return FakeTokenStore()


class FakeBot:
    def __init__(self) -> None:
        self.router = None
        self.started = False
        self.closed = False
        self.start_args = None
        self.sent: list[tuple[str, str]] = []

    async def start(self, *args, **kwargs) -> None:
        self.started = True
        self.start_args = (args, kwargs)
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True

    async def send_message(self, channel: str, content: str) -> None:
        self.sent.append((channel, content))


@pytest.fixture
def fake_bot() -> FakeBot:
    return FakeBot()
