"""Tests for the OpenAI-compatible LLM adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from bot.config import LLMEndpoint, LLMSettings
from bot.services.llm import LLMMessage, ToolDefinition
from bot.services.llm_openai import OpenAILLMProvider


def make_settings(**overrides: object) -> LLMSettings:
    values: dict[str, object] = {
        "endpoints": {
            "test": LLMEndpoint(
                base_url="https://llm.example/v1",
                api_key=SecretStr("secret"),
                default_model="test-model",
            )
        },
        "default_endpoint": "test",
    }
    values.update(overrides)
    return LLMSettings(**values)  # type: ignore[arg-type]


def make_provider(responses: list[object]) -> tuple[OpenAILLMProvider, list[dict]]:
    provider = OpenAILLMProvider(make_settings())
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    completions = SimpleNamespace(create=create)
    provider._clients["test"] = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    return provider, calls


def test_provider_uses_named_endpoint_base_url() -> None:
    provider = OpenAILLMProvider(make_settings())
    assert str(provider._client("test").base_url).startswith("https://llm.example/v1")


def test_clients_are_cached_per_endpoint() -> None:
    endpoints = {
        "alpha": LLMEndpoint(
            base_url="https://alpha.example/v1", api_key=SecretStr("a")
        ),
        "beta": LLMEndpoint(
            base_url="https://beta.example/v1", api_key=SecretStr("b")
        ),
    }
    provider = OpenAILLMProvider(
        make_settings(endpoints=endpoints, default_endpoint="alpha")
    )
    assert provider._client("ALPHA") is provider._client("alpha")
    assert provider._client("alpha") is not provider._client("beta")
    assert str(provider._client("beta").base_url).startswith("https://beta.example/v1")


def test_unknown_endpoint_is_rejected() -> None:
    provider = OpenAILLMProvider(make_settings())
    with pytest.raises(ValueError, match="Unknown LLM endpoint"):
        provider._client("missing")


def test_complete_forwards_requested_model() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))]
    )
    provider, calls = make_provider([response])

    result = asyncio.run(
        provider.complete(
            [LLMMessage(role="user", content="hi")],
            endpoint="test",
            model="other-model",
        )
    )

    assert result == "hello"
    assert calls[0]["model"] == "other-model"
    assert calls[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_empty_model_is_rejected() -> None:
    provider, calls = make_provider([])
    with pytest.raises(ValueError, match="model name is required"):
        asyncio.run(
            provider.complete(
                [LLMMessage(role="user", content="hi")], endpoint="test", model="  "
            )
        )
    assert calls == []


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
            endpoint="test",
            model="test-model",
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
    assert calls[0]["model"] == "test-model"
    assert calls[0]["tools"][0]["function"]["name"] == "web_search"


def test_message_payload_includes_tool_metadata() -> None:
    message = LLMMessage(role="tool", content="result", tool_call_id="call-1")
    from bot.services.llm_openai import OpenAILLMProvider as Provider

    assert Provider._payload([message]) == [
        {"role": "tool", "content": "result", "tool_call_id": "call-1"}
    ]
