"""Stage A tests for AI chat personas, permissions and settings commands."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pytest
from pydantic import SecretStr

from bot.config import LLMEndpoint
from bot.core.event_router import CommandContext, EventRouter
from bot.core.module_manager import ModuleManager
from bot.core.permissions import PermissionService
from bot.modules.ai_chat import AIChatModule
from bot.modules.ai_chat.config import AIChatConfig
from bot.modules.ai_chat.personas import (
    format_system_prompt,
    is_valid_persona_name,
    load_personas,
)
from bot.services import Services
from bot.services.llm import LLMResponse, ToolCall


@dataclass
class AllowEntry:
    user_id: str
    login: str


class FakeChannels:
    def __init__(self) -> None:
        self.persona: str | None = None
        self.endpoint: str | None = None
        self.model: str | None = None

    async def get(self, channel_id: str):
        if self.persona is None and self.endpoint is None and self.model is None:
            return None
        return type(
            "Channel",
            (),
            {
                "persona": self.persona or "",
                "endpoint": self.endpoint,
                "model": self.model,
            },
        )()

    async def set_persona(self, channel_id: str, persona: str) -> None:
        self.persona = persona

    async def set_model(self, channel_id: str, endpoint: str, model: str) -> None:
        self.endpoint = endpoint
        self.model = model


class FakeAllowlist:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], AllowEntry] = {}

    async def add(self, channel_id: str, user_id: str, login: str) -> None:
        self.entries[(channel_id, user_id)] = AllowEntry(user_id, login)

    async def remove(self, channel_id: str, user_id: str) -> bool:
        return self.entries.pop((channel_id, user_id), None) is not None

    async def list(self, channel_id: str) -> list[AllowEntry]:
        return [entry for (cid, _), entry in self.entries.items() if cid == channel_id]

    async def contains(self, channel_id: str, user_id: str) -> bool:
        return (channel_id, user_id) in self.entries


class FakeAILogger:
    def request(self, _messages: object) -> None:
        return None

    def reasoning(self, _text: str | None) -> None:
        return None

    def tool(self, *_args: object) -> None:
        return None

    def response(self, _text: str | None) -> None:
        return None

    def error(self, _message: str) -> None:
        return None

    def close(self) -> None:
        return None


class FakeThreads:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    async def get(self, thread_id: str):
        data = self.records.get(thread_id)
        if data is None:
            return None
        return type("Thread", (), {"messages": data["messages"]})()

    async def save(
        self, thread_id, channel_id, user_id, persona, messages
    ) -> None:
        self.records[thread_id] = {"messages": messages}


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list] = []
        self.endpoints: list[str] = []
        self.models: list[str] = []

    async def complete_with_tools(
        self, messages, *, endpoint=None, model=None, tools=(), max_tokens=None
    ):
        self.calls.append(list(messages))
        self.endpoints.append(endpoint)
        self.models.append(model)
        return self.responses.pop(0)


class FakeUsers:
    def __init__(self) -> None:
        self.record = type("User", (), {"id": "55", "login": "target"})()

    async def get_by_login(self, login: str):
        return self.record if login == "target" else None


def make_ctx(args: list[str], author_id: str = "42") -> tuple[CommandContext, list[str]]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command="chatset",
        args=args,
        author_id=author_id,
        author_name="admin",
        channel_id="7",
        channel_name="channel",
        content="!chatset",
        reply=reply,
    )
    return ctx, replies


def make_chat_ctx(
    content: str = "!chat hello", author_id: str = "99", author_login: str = "chatter"
):
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        command="chat",
        args=content.split()[1:],
        author_id=author_id,
        author_name="chatter",
        author_login=author_login,
        channel_id="7",
        channel_name="channel",
        content=content,
        reply=reply,
        thread_id="thread-1",
    )
    return ctx, replies


def make_module(settings, monkeypatch, personas=None, config=None, llm=None, threads=None):
    if not settings.llm.endpoints:
        settings.llm.endpoints = {
            "test": LLMEndpoint(
                base_url="https://llm.example/v1",
                api_key=SecretStr("key"),
                default_model="test-model",
            )
        }
        settings.llm.default_endpoint = "test"
    channels = FakeChannels()
    allowlist = FakeAllowlist()
    monkeypatch.setattr("bot.modules.ai_chat.module.AiChatChannelRepository", lambda _db: channels)
    monkeypatch.setattr(
        "bot.modules.ai_chat.module.AiChatAllowlistRepository", lambda _db: allowlist
    )
    monkeypatch.setattr(
        "bot.modules.ai_chat.module.AiChatThreadRepository",
        lambda _db: threads or FakeThreads(),
    )
    monkeypatch.setattr("bot.modules.ai_chat.module.UserRepository", lambda _db: FakeUsers())
    monkeypatch.setattr(
        "bot.modules.ai_chat.module.AILogger", lambda _path: FakeAILogger()
    )
    monkeypatch.setattr(
        "bot.modules.ai_chat.module.load_personas",
        lambda _dir: personas if personas is not None else {"generic.md": "Be nice."},
    )
    context = type(
        "Context",
        (),
        {
            "settings": settings,
            "config": config or {},
            "router": EventRouter(),
            "services": Services(
                permissions=PermissionService(settings.admin_user_ids), llm=llm
            ),
            "log": logging.getLogger("test.ai_chat"),
        },
    )()
    module = AIChatModule(context)
    return module, channels, allowlist


def test_config_defaults(settings) -> None:
    config = AIChatConfig.model_validate(settings.module.get("ai_chat", {}))
    assert config.personas_dir == "personas"
    assert config.default_persona == "generic.md"
    assert config.tools == ["web_search", "chat_messages"]


def test_persona_name_validation() -> None:
    assert is_valid_persona_name("generic.md")
    assert not is_valid_persona_name("../secret.md")
    assert not is_valid_persona_name("nested/persona.md")
    assert not is_valid_persona_name("persona.txt")


def test_load_personas(tmp_path) -> None:
    (tmp_path / "generic.md").write_text("Be nice.", encoding="utf-8")
    (tmp_path / "angry.md").write_text("Be rude.", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    assert load_personas(tmp_path) == {"angry.md": "Be rude.", "generic.md": "Be nice."}


def test_format_system_prompt_placeholders() -> None:
    from datetime import UTC, datetime

    prompt = format_system_prompt(
        "Date {date} time {time} user {username} id {user_id} keep {other}",
        username="chatter",
        user_id="99",
        now=datetime(2026, 11, 22, 7, 30, 15, tzinfo=UTC),
    )
    assert prompt == (
        "Date 2026-11-22 time 07:30:15 user chatter id 99 keep {other}"
    )


def test_owner_permission(settings) -> None:
    ctx, _ = make_ctx([], author_id="7")
    permissions = PermissionService(settings.admin_user_ids)
    assert permissions.can_manage_channel(ctx)

    ctx, _ = make_ctx([], author_id="99")
    assert not permissions.can_manage_channel(ctx)


async def test_chatset_prompt(settings, monkeypatch) -> None:
    module, channels, _ = make_module(
        settings, monkeypatch, {"generic.md": "Be nice."}
    )
    await module.setup()
    ctx, replies = make_ctx(["prompt", "generic.md"])

    await module.on_chatset(ctx)

    assert channels.persona == "generic.md"
    assert replies == ["Persona set to generic.md for this channel."]


async def test_chatset_unknown_persona(settings, monkeypatch) -> None:
    module, _channels, _ = make_module(settings, monkeypatch, {"generic.md": "Be nice."})
    await module.setup()
    ctx, replies = make_ctx(["prompt", "missing.md"])

    await module.on_chatset(ctx)

    assert "Unknown persona" in replies[0]


async def test_chatset_model_with_named_endpoint(settings, monkeypatch) -> None:
    module, channels, _ = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["model", "TEST/custom-model"])

    await module.on_chatset(ctx)

    assert (channels.endpoint, channels.model) == ("test", "custom-model")
    assert replies == ["Model set to test/custom-model for this channel."]


async def test_chatset_model_uses_default_endpoint(settings, monkeypatch) -> None:
    module, channels, _ = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["model", "plain-model"])

    await module.on_chatset(ctx)

    assert (channels.endpoint, channels.model) == ("test", "plain-model")
    assert replies == ["Model set to test/plain-model for this channel."]


async def test_chatset_model_rejects_unknown_endpoint(settings, monkeypatch) -> None:
    module, channels, _ = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["model", "nope/model"])

    await module.on_chatset(ctx)

    assert (channels.endpoint, channels.model) == (None, None)
    assert replies == ["Unknown endpoint. Available: test"]


async def test_chatset_model_rejects_invalid_model_name(settings, monkeypatch) -> None:
    module, channels, _ = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["model", "test/bad model"])

    await module.on_chatset(ctx)

    assert (channels.endpoint, channels.model) == (None, None)
    assert replies == ["Invalid model name."]


async def test_chatset_model_without_args_shows_status(settings, monkeypatch) -> None:
    module, _channels, _ = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["model"])

    await module.on_chatset(ctx)

    assert replies == ["Model: test/test-model. Available endpoints: test"]


async def test_chat_uses_channel_model(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="Hi!")])
    module, _channels, allowlist = make_module(
        settings, monkeypatch, llm=llm, config={"tools": [], "cooldown_seconds": 0}
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    set_ctx, _ = make_ctx(["model", "test/chosen-model"])
    await module.on_chatset(set_ctx)

    chat_ctx, replies = make_chat_ctx()
    await module.on_chat(chat_ctx)

    assert replies == ["Hi!"]
    assert llm.endpoints == ["test"]
    assert llm.models == ["chosen-model"]


async def test_chat_without_any_model_reports_setup(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="should not happen")])
    module, _channels, allowlist = make_module(
        settings, monkeypatch, llm=llm, config={"tools": [], "cooldown_seconds": 0}
    )
    module.context.settings.llm.endpoints = {}
    module.context.settings.llm.default_endpoint = None
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    ctx, replies = make_chat_ctx()

    await module.on_chat(ctx)

    assert "!chatset model" in replies[0]
    assert llm.calls == []


async def test_chatset_allow_list_remove(settings, monkeypatch) -> None:
    module, _channels, allowlist = make_module(settings, monkeypatch)
    await module.setup()

    ctx, replies = make_ctx(["allow", "@target"])
    await module.on_chatset(ctx)
    assert ("7", "55") in allowlist.entries
    assert "Added @target" in replies[0]

    ctx, replies = make_ctx(["list"])
    await module.on_chatset(ctx)
    assert "@target" in replies[0]

    ctx, replies = make_ctx(["remove", "target"])
    await module.on_chatset(ctx)
    assert ("7", "55") not in allowlist.entries
    assert "Removed @target" in replies[0]


async def test_chatset_requires_owner(settings, monkeypatch) -> None:
    module, _channels, _ = make_module(settings, monkeypatch)
    await module.setup()
    ctx, replies = make_ctx(["list"], author_id="99")

    await module.on_chatset(ctx)

    assert replies == ["Only the bot owner or channel owner can do that."]


async def test_chat_requires_allowlist(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="should not happen")])
    module, _channels, _allowlist = make_module(settings, monkeypatch, llm=llm)
    await module.setup()
    ctx, replies = make_chat_ctx()

    await module.on_chat(ctx)

    assert replies == []
    assert llm.calls == []


async def test_chat_replies_and_stores_thread(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="Hello there!")])
    threads = FakeThreads()
    module, _channels, allowlist = make_module(
        settings,
        monkeypatch,
        llm=llm,
        threads=threads,
        config={"tools": [], "cooldown_seconds": 0},
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    ctx, replies = make_chat_ctx()

    await module.on_chat(ctx)

    assert replies == ["Hello there!"]
    assert llm.calls[0][0].content.startswith("Be nice.")
    assert "thread-1" in threads.records
    assert threads.records["thread-1"]["messages"][-1] == {
        "role": "assistant",
        "content": "Hello there!",
    }


@pytest.mark.parametrize("alias", ["chat", "claude", "ai"])
async def test_chat_aliases_dispatch_and_strip_their_prefix(
    settings, monkeypatch, alias
) -> None:
    llm = FakeLLM([LLMResponse(content="Hi!")])
    module, _channels, allowlist = make_module(
        settings,
        monkeypatch,
        llm=llm,
        config={"tools": [], "cooldown_seconds": 0},
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    ctx, replies = make_chat_ctx(content=f"!{alias} hello there")
    ctx.command = alias

    await module.context.router.dispatch_command(ctx)

    assert replies == ["Hi!"]
    assert llm.calls[0][-1].content == "hello there"


async def test_chat_alias_prefix_needs_a_word_boundary(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="Hi!")])
    module, _channels, allowlist = make_module(
        settings,
        monkeypatch,
        llm=llm,
        config={"tools": [], "cooldown_seconds": 0},
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    ctx, _replies = make_chat_ctx(content="!aime hello")
    ctx.command = "ai"

    await module.on_chat(ctx)

    assert llm.calls[0][-1].content == "!aime hello"


async def test_chat_executes_tool_calls_then_answers(settings, monkeypatch) -> None:
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="call-1", name="fake_tool", arguments={})],
        ),
        LLMResponse(content="Final answer."),
    ]
    llm = FakeLLM(responses)
    threads = FakeThreads()

    class FakeTool:
        name = "fake_tool"
        description = "A test tool"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, _arguments: dict, _ctx: CommandContext) -> str:
            return "tool result"

    module, _channels, allowlist = make_module(
        settings,
        monkeypatch,
        llm=llm,
        threads=threads,
        config={"tools": [], "cooldown_seconds": 0},
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    tool = FakeTool()
    module._tools.append(tool)
    module._tool_map[tool.name] = tool
    ctx, replies = make_chat_ctx()

    await module.on_chat(ctx)

    assert replies == ["Final answer."]
    assert len(llm.calls) == 2
    assert any(getattr(message, "content", None) == "tool result" for message in llm.calls[1])


async def test_chat_prompt_identifies_requester_and_time(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="Hi!")])
    module, _channels, allowlist = make_module(
        settings,
        monkeypatch,
        personas={"generic.md": "Date {date} time {time} user {username}"},
        llm=llm,
        config={"tools": [], "cooldown_seconds": 0},
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    ctx, _replies = make_chat_ctx(content="@testbot what is today's date?")

    await module.on_chat(ctx)

    system_prompt = llm.calls[0][0].content
    assert re.search(r"Date \d{4}-\d{2}-\d{2}", system_prompt)
    assert re.search(r"time \d{2}:\d{2}:\d{2}", system_prompt)
    assert "user chatter" in system_prompt
    assert "@chatter (Twitch ID 99)" in system_prompt
    assert llm.calls[0][-1].content == "what is today's date?"


async def test_chat_continues_existing_thread_context(settings, monkeypatch) -> None:
    llm = FakeLLM([LLMResponse(content="Remembered.")])
    threads = FakeThreads()
    threads.records["thread-1"] = {
        "messages": [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]
    }
    module, _channels, allowlist = make_module(
        settings,
        monkeypatch,
        llm=llm,
        threads=threads,
        config={"tools": [], "cooldown_seconds": 0},
    )
    await module.setup()
    await allowlist.add("7", "99", "chatter")
    ctx, _replies = make_chat_ctx()

    await module.on_chat(ctx)

    contents = [getattr(message, "content", None) for message in llm.calls[0]]
    assert "earlier question" in contents
    assert "earlier answer" in contents


def test_discovery_finds_ai_chat(settings) -> None:
    manager = ModuleManager(settings, EventRouter(), Services())
    assert "ai_chat" in manager.discover()
