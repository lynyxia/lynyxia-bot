"""Tests for AI chat tools."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from bot.core.event_router import CommandContext
from bot.database.repositories.chat_message import ChatSample, Chatter
from bot.modules.ai_chat.tools import (
    ChatMessagesTool,
    ChatterSamplesTool,
    ListChattersTool,
    SearchChatTool,
    WebSearchTool,
)


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


# --- Chat-history discovery and sampling tools -----------------------------


NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


class HistoryMessages:
    def __init__(self, chatters=(), samples=(), results=()) -> None:
        self.chatters = list(chatters)
        self.samples = list(samples)
        self.results = list(results)
        self.calls: dict[str, tuple] = {}

    async def list_chatters(self, channel_id: str, **kwargs) -> list[Chatter]:
        self.calls["chatters"] = (channel_id, kwargs)
        return self.chatters

    async def get_samples_by_users(
        self, channel_id: str, user_ids, **kwargs
    ) -> list[ChatSample]:
        self.calls["samples"] = (channel_id, list(user_ids), kwargs)
        return self.samples

    async def search(self, channel_id: str, term: str, **kwargs) -> list[ChatSample]:
        self.calls["search"] = (channel_id, term, kwargs)
        return self.results


class HistoryUsers:
    async def get_by_login(self, login: str):
        user_ids = {"alice": "1", "bob": "2"}
        user_id = user_ids.get(login.lower())
        if user_id is None:
            return None
        return type("User", (), {"id": user_id})()


async def test_list_chatters_is_channel_scoped_and_excludes_bot() -> None:
    messages = HistoryMessages(
        chatters=[
            Chatter("1", "alice", "Alice", 12, NOW),
            Chatter("2", "bob", "Bob", 5, NOW),
        ]
    )
    tool = ListChattersTool(messages, bot_id="42")  # type: ignore[arg-type]

    result = await tool.execute(
        {"since_days": 7, "min_messages": 2, "limit": 10}, make_ctx()
    )

    assert "alice (Alice): 12 messages" in result
    assert "bob (Bob): 5 messages" in result
    channel, kwargs = messages.calls["chatters"]
    assert channel == "7"
    assert kwargs["exclude_user_ids"] == ("42",)
    assert kwargs["min_messages"] == 2
    assert kwargs["limit"] == 10
    assert kwargs["since"] is not None


async def test_list_chatters_handles_empty_and_bad_arguments() -> None:
    messages = HistoryMessages()
    tool = ListChattersTool(messages, bot_id="42", max_chatters=10)  # type: ignore[arg-type]

    result = await tool.execute({"since_days": "oops", "limit": 999}, make_ctx())

    assert result == "No chatters found in this channel."
    _channel, kwargs = messages.calls["chatters"]
    assert kwargs["limit"] == 10
    assert kwargs["min_messages"] == 3


async def test_samples_read_many_users_in_one_call() -> None:
    messages = HistoryMessages(
        samples=[ChatSample("1", "Alice", "that's hilarious", NOW)]
    )
    tool = ChatterSamplesTool(HistoryUsers(), messages, bot_id="42")  # type: ignore[arg-type]

    result = await tool.execute(
        {
            "usernames": ["alice", "@bob"],
            "per_user": 2,
            "selection": "recent",
            "since_days": 7,
        },
        make_ctx(),
    )

    assert result == "[2026-01-02] Alice: that's hilarious"
    channel, user_ids, kwargs = messages.calls["samples"]
    assert channel == "7"
    assert user_ids == ["1", "2"]
    assert kwargs["per_user"] == 2
    assert kwargs["random"] is False
    assert kwargs["since"] is not None


async def test_samples_default_to_active_chatters_when_no_usernames() -> None:
    messages = HistoryMessages(
        chatters=[Chatter("1", "alice", "Alice", 3, NOW)], samples=[]
    )
    tool = ChatterSamplesTool(HistoryUsers(), messages, bot_id="42")  # type: ignore[arg-type]

    result = await tool.execute({}, make_ctx())

    assert result == "No messages found for those chatters."
    assert messages.calls["chatters"][0] == "7"
    assert messages.calls["samples"][1] == ["1"]


async def test_samples_skip_unknown_users_and_the_bot() -> None:
    messages = HistoryMessages()
    tool = ChatterSamplesTool(HistoryUsers(), messages, bot_id="2")  # type: ignore[arg-type]

    result = await tool.execute({"usernames": ["nobody", "bob"]}, make_ctx())

    assert result == "No matching chatters found in this channel."
    assert "samples" not in messages.calls


async def test_search_is_channel_scoped_and_can_filter_by_user() -> None:
    messages = HistoryMessages(results=[ChatSample("1", "Alice", "so cute!", NOW)])
    tool = SearchChatTool(HistoryUsers(), messages, bot_id="42")  # type: ignore[arg-type]

    result = await tool.execute(
        {"query": "cute", "username": "@alice", "limit": 5}, make_ctx()
    )

    assert "Alice: so cute!" in result
    channel, term, kwargs = messages.calls["search"]
    assert channel == "7"
    assert term == "cute"
    assert kwargs["user_id"] == "1"
    assert kwargs["exclude_user_ids"] == ("42",)
    assert kwargs["limit"] == 5


async def test_search_requires_a_query() -> None:
    messages = HistoryMessages()
    tool = SearchChatTool(HistoryUsers(), messages, bot_id="42")  # type: ignore[arg-type]

    result = await tool.execute({}, make_ctx())

    assert result == "A search query is required."
    assert "search" not in messages.calls
