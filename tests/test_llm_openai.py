"""Tests for the OpenAI-compatible LLM adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

from bot.config import LLMSettings
from bot.services.llm import LLMMessage, ToolDefinition
from bot.services.llm_openai import OpenAILLMProvider


def make_provider(responses: list[object]) -> tuple[OpenAILLMProvider, object]:
    settings = LLMSettings(
        provider="openai",
        api_key=SecretStr("secret"),
        base_url="https://llm.example/v1",
        model="test-model",
        timeout_seconds=30,
    )
    provider = OpenAILLMProvider(settings)
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    completions = SimpleNamespace(create=create)
    provider._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    return provider, calls


def test_provider_accepts_third_party_base_url() -> None:
    settings = LLMSettings(
        api_key=SecretStr("key"),
        base_url="https://third-party.example/v1",
        model="model",
    )
    provider = OpenAILLMProvider(settings)
    assert str(provider._client.base_url).startswith("https://third-party.example/v1")


def test_complete_returns_content() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))]
    )
    provider, calls = make_provider([response])

    result = asyncio.run(provider.complete([LLMMessage(role="user", content="hi")]))

    assert result == "hello"
    assert calls[0]["model"] == "test-model"
    assert calls[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_complete_with_tools_maps_calls_and_reasoning() -> None:
    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="web_search", arguments='{"query":"cats"}'),
    )
    message = SimpleNamespace(
        content="answer",
        tool_calls=[call],
        reasoning_content="thinking",
    )
    usage = SimpleNamespace(model_dump=lambda: {"total_tokens": 12})
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=usage
    )
    provider, calls = make_provider([response])

    result = asyncio.run(
        provider.complete_with_tools(
            [LLMMessage(role="user", content="hi")],
            tools=[
                ToolDefinition(
                    name="web_search",
                    description="Search",
                    parameters={"type": "object"},
                )
            ],
        )
    )

    assert result.content == "answer"
    assert result.reasoning == "thinking"
    assert result.tool_calls[0].name == "web_search"
    assert result.tool_calls[0].arguments == {"query": "cats"}
    assert result.usage == {"total_tokens": 12}
    assert calls[0]["tools"][0]["function"]["name"] == "web_search"


def test_message_payload_includes_tool_metadata() -> None:
    message = LLMMessage(
        role="tool", content="result", tool_call_id="call-1"
    )
    from bot.services.llm_openai import OpenAILLMProvider as Provider

    assert Provider._payload([message]) == [
        {"role": "tool", "content": "result", "tool_call_id": "call-1"}
    ]
