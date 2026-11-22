"""Tools the LLM can call during an AI chat reply."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from bot.core.event_router import CommandContext
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.database.repositories.user import UserRepository


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
