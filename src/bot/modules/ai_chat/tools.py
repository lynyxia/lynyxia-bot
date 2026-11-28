"""Tools the LLM can call during an AI chat reply."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx

from bot.core.event_router import CommandContext
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.database.repositories.user import UserRepository


def _as_int(value: object, default: int, *, low: int, high: int) -> int:
    """Parse a tool argument into a bounded integer, ignoring bad input."""
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(parsed, high))


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    async def execute(self, arguments: dict[str, Any], ctx: CommandContext) -> str: ...


class WebSearchTool:
    name = "web_search"
    description = "Search the web using a SearXNG JSON instance."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"}
        },
        "required": ["query"],
    }

    def __init__(
        self, url: str | None, *, timeout: float, max_results: int
    ) -> None:
        self._url = url.rstrip("/") if url else None
        self._timeout = timeout
        self._max_results = max_results
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._url:
            self._client = httpx.AsyncClient(timeout=self._timeout)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def execute(self, arguments: dict[str, Any], _ctx: CommandContext) -> str:
        if self._client is None or not self._url:
            return "Web search is not configured."
        query = str(arguments.get("query", "")).strip()[:200]
        if not query:
            return "A search query is required."
        try:
            response = await self._client.get(
                f"{self._url}/search", params={"q": query, "format": "json"}
            )
            response.raise_for_status()
            results = response.json().get("results", [])[: self._max_results]
        except Exception:
            return "Web search failed."
        lines = []
        for result in results:
            title = result.get("title", "Untitled")
            content = result.get("content", "")
            url = result.get("url", "")
            lines.append(f"{title}: {content} ({url})")
        return "\n".join(lines) or "No results found."


class ChatMessagesTool:
    name = "chat_messages"
    description = (
        "Read recent chat messages from a user in this channel to answer "
        "questions about them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Twitch login to look up"},
            "limit": {"type": "integer", "description": "Maximum messages to read"},
        },
        "required": ["username"],
    }

    def __init__(
        self,
        users: UserRepository,
        messages: ChatMessageRepository,
        *,
        max_messages: int,
    ) -> None:
        self._users = users
        self._messages = messages
        self._max_messages = max_messages

    async def execute(self, arguments: dict[str, Any], ctx: CommandContext) -> str:
        username = str(arguments.get("username", "")).strip().lstrip("@")
        if not username:
            return "A username is required."
        user = await self._users.get_by_login(username)
        if user is None:
            return f"No messages found for {username}."
        try:
            limit = int(arguments.get("limit", self._max_messages))
        except (TypeError, ValueError):
            limit = self._max_messages
        limit = max(1, min(limit, self._max_messages))

        messages = await self._messages.get_recent_by_user(ctx.channel_id, user.id, limit)
        lines = [
            f"[{message.sent_at:%Y-%m-%d %H:%M:%S}] {message.username}: {message.content}"
            for message in reversed(messages)
            if message.sent_at is not None
        ]
        return "\n".join(lines) or f"No messages found for {username}."


class ListChattersTool:
    name = "list_chatters"
    description = (
        "List chatters in this channel with their message counts, most active "
        "first. Call this before sample_chatter_messages or search_chat when "
        "you need to know which usernames are available."
    )
    parameters = {
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": "Only count messages from the last N days",
                "minimum": 1,
                "maximum": 3650,
            },
            "min_messages": {
                "type": "integer",
                "description": "Minimum messages a chatter must have",
                "minimum": 1,
                "maximum": 1000,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of chatters to return",
                "minimum": 1,
                "maximum": 50,
            },
        },
    }

    def __init__(
        self,
        messages: ChatMessageRepository,
        *,
        bot_id: str,
        max_chatters: int = 50,
    ) -> None:
        self._messages = messages
        self._bot_id = bot_id
        self._max_chatters = max_chatters

    async def execute(self, arguments: dict[str, Any], ctx: CommandContext) -> str:
        days = _as_int(arguments.get("since_days"), 30, low=1, high=3650)
        min_messages = _as_int(arguments.get("min_messages"), 3, low=1, high=1000)
        limit = _as_int(arguments.get("limit"), 20, low=1, high=self._max_chatters)
        chatters = await self._messages.list_chatters(
            ctx.channel_id,
            since=_since(days),
            min_messages=min_messages,
            limit=limit,
            exclude_user_ids=(self._bot_id,),
        )
        lines = [
            f"{row.login} ({row.display_name}): {row.message_count} messages, "
            f"last seen {row.last_seen_at:%Y-%m-%d}"
            for row in chatters
        ]
        return "\n".join(lines) or "No chatters found in this channel."


class ChatterSamplesTool:
    name = "sample_chatter_messages"
    description = (
        "Read a few messages from several chatters at once in this channel. "
        "Pass the logins returned by list_chatters; omit usernames to sample "
        "the most active chatters. Choose 'random' instead of 'recent' to "
        "avoid recency bias when judging someone's chat history."
    )
    parameters = {
        "type": "object",
        "properties": {
            "usernames": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Twitch logins to sample",
            },
            "per_user": {
                "type": "integer",
                "description": "Messages to read per chatter",
                "minimum": 1,
                "maximum": 5,
            },
            "selection": {
                "type": "string",
                "enum": ["recent", "random"],
                "description": "How to pick each chatter's messages",
            },
            "since_days": {
                "type": "integer",
                "description": "Only read messages from the last N days",
                "minimum": 1,
                "maximum": 3650,
            },
        },
    }

    def __init__(
        self,
        users: UserRepository,
        messages: ChatMessageRepository,
        *,
        bot_id: str,
        max_users: int = 20,
        max_per_user: int = 5,
        max_chars: int = 8000,
    ) -> None:
        self._users = users
        self._messages = messages
        self._bot_id = bot_id
        self._max_users = max_users
        self._max_per_user = max_per_user
        self._max_chars = max_chars

    async def execute(self, arguments: dict[str, Any], ctx: CommandContext) -> str:
        days = _as_int(arguments.get("since_days"), 30, low=1, high=3650)
        per_user = _as_int(
            arguments.get("per_user"), 3, low=1, high=self._max_per_user
        )
        selection = str(arguments.get("selection", "random")).lower()
        random = selection != "recent"

        raw = arguments.get("usernames") or []
        if isinstance(raw, str):
            raw = [raw]
        names = [
            str(name).strip().lstrip("@") for name in raw if str(name).strip()
        ][: self._max_users]
        user_ids: list[str] = []
        if names:
            for name in names:
                user = await self._users.get_by_login(name)
                if user is not None and user.id != self._bot_id:
                    user_ids.append(user.id)
        else:
            chatters = await self._messages.list_chatters(
                ctx.channel_id,
                since=_since(days),
                min_messages=1,
                limit=self._max_users,
                exclude_user_ids=(self._bot_id,),
            )
            user_ids = [chatter.user_id for chatter in chatters]
        if not user_ids:
            return "No matching chatters found in this channel."

        samples = await self._messages.get_samples_by_users(
            ctx.channel_id,
            user_ids,
            per_user=per_user,
            random=random,
            since=_since(days),
        )
        lines = [
            f"[{sample.sent_at:%Y-%m-%d}] {sample.username}: {sample.content}"
            for sample in samples
        ]
        return "\n".join(lines)[: self._max_chars] or (
            "No messages found for those chatters."
        )


class SearchChatTool:
    name = "search_chat"
    description = (
        "Search this channel's chat history for a word or phrase, newest "
        "first. Optionally restrict the search to one chatter. Useful for "
        "finding who other people called cute, funny, and so on."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Word or phrase to look for",
                "maxLength": 100,
            },
            "username": {
                "type": "string",
                "description": "Only search this chatter's messages",
            },
            "since_days": {
                "type": "integer",
                "description": "Only search the last N days",
                "minimum": 1,
                "maximum": 3650,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum messages to return",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        users: UserRepository,
        messages: ChatMessageRepository,
        *,
        bot_id: str,
        max_results: int = 50,
        max_chars: int = 8000,
    ) -> None:
        self._users = users
        self._messages = messages
        self._bot_id = bot_id
        self._max_results = max_results
        self._max_chars = max_chars

    async def execute(self, arguments: dict[str, Any], ctx: CommandContext) -> str:
        query = str(arguments.get("query", "")).strip()[:100]
        if not query:
            return "A search query is required."
        days = _as_int(arguments.get("since_days"), 30, low=1, high=3650)
        limit = _as_int(arguments.get("limit"), 20, low=1, high=self._max_results)

        user_id: str | None = None
        raw_name = arguments.get("username")
        if raw_name:
            name = str(raw_name).strip().lstrip("@")
            user = await self._users.get_by_login(name)
            if user is None:
                return f"No messages found for {name}."
            user_id = user.id

        messages = await self._messages.search(
            ctx.channel_id,
            query,
            since=_since(days),
            limit=limit,
            user_id=user_id,
            exclude_user_ids=(self._bot_id,),
        )
        lines = [
            f"[{message.sent_at:%Y-%m-%d}] {message.username}: {message.content}"
            for message in messages
        ]
        return "\n".join(lines)[: self._max_chars] or "No matching messages found."
