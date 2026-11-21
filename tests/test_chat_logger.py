"""Unit tests for the batched chat logger module."""

from __future__ import annotations

import asyncio
import logging

import pytest
from pydantic import ValidationError

from bot.core.event_router import ChatMessageEvent, EventRouter
from bot.modules.base import ModuleContext
from bot.modules.chat_logger import ChatLoggerModule
from bot.modules.chat_logger.config import ChatLoggerConfig
from bot.services import Services


class FakeRepository:
    def __init__(self) -> None:
        self.batches: list[list[ChatMessageEvent]] = []
        self.called = asyncio.Event()

    async def record_batch(self, events: list[ChatMessageEvent]) -> int:
        self.batches.append(list(events))
        self.called.set()
        return len(events)


def make_event(index: int = 1) -> ChatMessageEvent:
    return ChatMessageEvent(
        author_id="42",
        author_login="chatter",
        author_name="Chatter",
        channel_id="7",
        channel_login="channel",
        channel_name="Channel",
        content=f"message {index}",
        message_id=f"msg-{index}",
        sent_at=None,
    )


def make_module(
    settings,
    monkeypatch,
    config: dict | None = None,
    repository: FakeRepository | None = None,
) -> tuple[ChatLoggerModule, FakeRepository]:
    fake = repository or FakeRepository()
    monkeypatch.setattr(
        "bot.modules.chat_logger.module.ChatMessageRepository", lambda _db: fake
    )
    context = ModuleContext(
        settings=settings,
        config=config or {},
        router=EventRouter(),
        services=Services(),
        log=logging.getLogger("test.chat_logger"),
    )
    return ChatLoggerModule(context), fake


def test_config_defaults_and_bounds() -> None:
    config = ChatLoggerConfig()
    assert config.batch_size == 50
    assert config.retention_days is None
    with pytest.raises(ValidationError):
        ChatLoggerConfig(batch_size=0)
    with pytest.raises(ValidationError):
        ChatLoggerConfig(flush_interval_seconds=0)


async def test_setup_registers_message_handler(settings, monkeypatch) -> None:
    module, _fake = make_module(settings, monkeypatch)
    await module.setup()

    await module.context.router.dispatch_event("message", make_event())

    assert module.pending_count == 1
    await module.stop()


async def test_batches_messages(settings, monkeypatch) -> None:
    module, fake = make_module(
        settings, monkeypatch, {"batch_size": 3, "flush_interval_seconds": 10}
    )
    await module.setup()
    await module.start()

    for index in range(3):
        await module.on_message(make_event(index))

    await asyncio.wait_for(fake.called.wait(), timeout=1)
    assert len(fake.batches[0]) == 3
    assert module.stored_count == 3
    await module.stop()


async def test_invalid_events_are_ignored(settings, monkeypatch) -> None:
    module, _fake = make_module(settings, monkeypatch)
    await module.setup()

    await module.on_message(
        ChatMessageEvent("", "", "", "7", "", "", "", None, None)
    )
    assert module.pending_count == 0
    await module.stop()


async def test_overflow_drops_without_blocking(settings, monkeypatch) -> None:
    module, _fake = make_module(settings, monkeypatch, {"queue_maxsize": 2})
    await module.setup()

    for index in range(4):
        await module.on_message(make_event(index))

    assert module.pending_count == 2
    assert module.dropped_count == 2


async def test_stop_drains_pending_messages(settings, monkeypatch) -> None:
    module, fake = make_module(
        settings, monkeypatch, {"batch_size": 100, "flush_interval_seconds": 10}
    )
    await module.setup()
    await module.start()

    await module.on_message(make_event())
    await module.stop()

    assert module.stored_count == 1
    assert len(fake.batches) == 1


async def test_module_discovery_finds_submodule_class(settings) -> None:
    from bot.core.module_manager import ModuleManager
    from bot.services import Services

    manager = ModuleManager(settings, EventRouter(), Services())
    assert "chat_logger" in manager.discover()
    assert manager.discover()["chat_logger"] is ChatLoggerModule
