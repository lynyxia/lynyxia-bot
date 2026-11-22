"""Tests for AI chat tools."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from bot.core.event_router import CommandContext
from bot.modules.ai_chat.tools import ChatMessagesTool, WebSearchTool


def make_ctx() -> CommandContext:
    async def reply(_text: str) -> None:
        return None

    return CommandContext(
        command="chat",
        args=[],
        author_id="99",
        author_name="chatter",
        channel_id="7",
        channel_name="channel",
        content="!chat",
        reply=reply,
    )


async def test_web_search_tool_parses_searxng_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Cats",
                        "content": "A article about cats",
                        "url": "https://example.com/cats",
                    }
                ]
            },
        )

    tool = WebSearchTool("https://searx.example", timeout=5, max_results=5)
    tool._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await tool.execute({"query": "cats"}, make_ctx())
    await tool.stop()

    assert "Cats" in result
    assert "https://example.com/cats" in result


async def test_web_search_tool_requires_configuration() -> None:
    tool = WebSearchTool(None, timeout=5, max_results=5)
    await tool.start()
    assert "not configured" in await tool.execute({"query": "cats"}, make_ctx())
    await tool.stop()


class FakeUser:
    id = "42"
    login = "kappa123"


class FakeUsers:
    async def get_by_login(self, login: str):
        return FakeUser() if login == "kappa123" else None


class FakeMessage:
    def __init__(self, username: str, content: str) -> None:
        self.username = username
        self.content = content
        self.sent_at = datetime(2021, 8, 11, 17, 25, 54, tzinfo=UTC)


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def get_recent_by_user(self, channel_id, user_id, limit):
        self.calls.append((channel_id, user_id, limit))
        return [FakeMessage("kappa123", "hello"), FakeMessage("kappa123", "world")]


async def test_chat_messages_tool_resolves_user_and_caps_limit() -> None:
    users = FakeUsers()
    messages = FakeMessages()
    tool = ChatMessagesTool(users, messages, max_messages=5)  # type: ignore[arg-type]

    result = await tool.execute(
        {"username": "@kappa123", "limit": 100}, make_ctx()
    )

    assert messages.calls == [("7", "42", 5)]
    assert "[2021-08-11 17:25:54] kappa123: hello" in result
    assert "world" in result


async def test_chat_messages_tool_unknown_user() -> None:
    tool = ChatMessagesTool(FakeUsers(), FakeMessages(), max_messages=5)  # type: ignore[arg-type]
    assert "No messages" in await tool.execute({"username": "nobody"}, make_ctx())
