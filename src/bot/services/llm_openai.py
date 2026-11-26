"""OpenAI-compatible LLM provider.

The OpenAI SDK's ``base_url`` option makes this work with named third-party
OpenAI-compatible endpoints, not just api.openai.com. The model is chosen per
request by the caller, so one provider can serve several channels.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from openai import AsyncOpenAI

from bot.config import LLMSettings
from bot.services.llm import (
    LLMMessage,
    LLMResponse,
    ToolCall,
    ToolDefinition,
)


class OpenAILLMProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._clients: dict[str, AsyncOpenAI] = {}

    def _client(self, endpoint: str) -> AsyncOpenAI:
        """Return a lazily created client for a configured endpoint name."""
        name = endpoint.strip().lower()
        config = self._settings.endpoints.get(name)
        if config is None:
            raise ValueError(f"Unknown LLM endpoint: {endpoint!r}")
        if name not in self._clients:
            self._clients[name] = AsyncOpenAI(
                api_key=config.api_key.get_secret_value() or "not-set",
                base_url=config.base_url,
                timeout=self._settings.timeout_seconds,
                max_retries=2,
            )
        return self._clients[name]

    @staticmethod
    def _check_model(model: str) -> str:
        name = model.strip()
        if not name:
            raise ValueError("A model name is required")
        return name

    @staticmethod
    def _payload(messages: Sequence[LLMMessage]) -> list[dict]:
        payload: list[dict] = []
        for message in messages:
            item: dict = {"role": message.role, "content": message.content}
            if message.tool_calls:
                item["tool_calls"] = message.tool_calls
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            payload.append(item)
        return payload

    @staticmethod
    def _tool_payload(tools: Sequence[ToolDefinition]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        endpoint: str,
        model: str,
        max_tokens: int | None = None,
    ) -> str:
        response = await self._client(endpoint).chat.completions.create(
            model=self._check_model(model),
            messages=self._payload(messages),
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def complete_with_tools(
        self,
        messages: Sequence[LLMMessage],
        *,
        endpoint: str,
        model: str,
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self._check_model(model),
            "messages": self._payload(messages),
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._tool_payload(tools)
        response = await self._client(endpoint).chat.completions.create(**kwargs)
        message = response.choices[0].message

        tool_calls = []
        for call in getattr(message, "tool_calls", None) or ():
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except (ValueError, TypeError):
                arguments = {}
            tool_calls.append(
                ToolCall(id=call.id, name=call.function.name, arguments=arguments)
            )

        reasoning = getattr(message, "reasoning_content", None) or getattr(
            message, "reasoning", None
        )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            reasoning=reasoning,
            usage=usage.model_dump() if hasattr(usage, "model_dump") else {},
            raw=response,
        )
