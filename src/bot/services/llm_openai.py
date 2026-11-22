"""OpenAI-compatible LLM provider.

The OpenAI SDK's ``base_url`` option makes this work with third-party
OpenAI-compatible endpoints, not just api.openai.com.
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
        self._model = settings.model
        self._client = AsyncOpenAI(
            api_key=settings.api_key.get_secret_value() if settings.api_key else "not-set",
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=2,
        )

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
        self, messages: Sequence[LLMMessage], *, max_tokens: int | None = None
    ) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=self._payload(messages),
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def complete_with_tools(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self._model,
            "messages": self._payload(messages),
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._tool_payload(tools)
        response = await self._client.chat.completions.create(**kwargs)
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
