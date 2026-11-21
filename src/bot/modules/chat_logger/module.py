"""Asynchronous, batched persistence of incoming chat messages."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from bot.core.event_router import ChatMessageEvent
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.modules.base import Module, ModuleContext
from bot.modules.chat_logger.config import ChatLoggerConfig


class ChatLoggerModule(Module):
    name = "chat_logger"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = ChatLoggerConfig.model_validate(context.config)
        self._queue: asyncio.Queue[ChatMessageEvent] = asyncio.Queue(
            maxsize=self.config.queue_maxsize
        )
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._repository: ChatMessageRepository | None = None
        self._stored = 0
        self._dropped = 0

    async def setup(self) -> None:
        self._repository = ChatMessageRepository(self.context.services.database)
        self.subscribe(self.context.router.on_event("message", self.on_message))
        if self.config.retention_days is not None:
            self.log.info(
                "Retention configured at %s day(s); cleanup scheduling is a future extension",
                self.config.retention_days,
            )

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="chat-logger-worker")

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=10)
            except TimeoutError:
                self._worker.cancel()
                with suppress(asyncio.CancelledError):
                    await self._worker
            self._worker = None
        await super().stop()
        self.log.info(
            "Chat logger stopped: %s stored, %s dropped", self._stored, self._dropped
        )

    @property
    def stored_count(self) -> int:
        return self._stored

    @property
    def dropped_count(self) -> int:
        return self._dropped

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    async def on_message(self, event: ChatMessageEvent) -> None:
        """Enqueue a message without doing any I/O on the event loop."""
        if not event.content or not event.channel_id or not event.author_id:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            self.log.debug(
                "Chat log queue full; dropped a message (%s dropped total)", self._dropped
            )

    async def _get_next(self) -> ChatMessageEvent | None:
        """Wait for a message, a flush interval, or a stop signal."""
        getter = asyncio.create_task(self._queue.get())
        stopper = asyncio.create_task(self._wake.wait())
        try:
            done, _pending = await asyncio.wait(
                {getter, stopper},
                timeout=self.config.flush_interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            return getter.result() if getter in done else None
        finally:
            for task in (getter, stopper):
                if not task.done():
                    task.cancel()
            await asyncio.gather(getter, stopper, return_exceptions=True)

    async def _run(self) -> None:
        batch: list[ChatMessageEvent] = []
        while True:
            item = await self._get_next()
            if item is not None:
                batch.append(item)
            while len(batch) < self.config.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if batch:
                await self._flush(batch)
                batch = []

            if self._stopping.is_set() and self._queue.empty():
                break

    async def _flush(self, batch: list[ChatMessageEvent]) -> None:
        if self._repository is None:
            return
        try:
            self._stored += await self._repository.record_batch(batch)
        except Exception as exc:
            # Avoid logging message contents or database parameter values.
            self.log.warning(
                "Failed to persist %s chat messages (%s)", len(batch), type(exc).__name__
            )
