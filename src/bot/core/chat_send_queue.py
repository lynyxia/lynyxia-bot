"""Global outbound chat send queue.

A single worker paces all chat messages, including replies from modules and
messages sent through ``TwitchService``. This avoids per-handler sleeps and
respects Twitch's account-wide chat rate limits.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

Send = Callable[[], Awaitable[None]]


class ChatSender(Protocol):
    async def submit(self, send: Send, channel_id: str) -> bool:
        """Queue a send; return ``False`` if it was dropped."""


class DirectChatSender:
    """Send immediately. Used by standalone tools and tests."""

    async def submit(self, send: Send, channel_id: str) -> bool:
        await send()
        return True


@dataclass
class _SendJob:
    send: Send
    channel_id: str


class ChatSendQueue:
    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.5,
        queue_maxsize: int = 100,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self._min_interval = min_interval_seconds
        self._queue: asyncio.Queue[_SendJob] = asyncio.Queue(maxsize=queue_maxsize)
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._stopping = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self.sent = 0
        self.dropped = 0
        self.retried = 0

    async def start(self) -> None:
        if self._worker is None:
            self._stopping.clear()
            self._worker = asyncio.create_task(self._run(), name="chat-send-queue")

    async def stop(self) -> None:
        self._stopping.set()
        if self._worker is not None:
            await self._worker
            self._worker = None

    async def submit(self, send: Send, channel_id: str) -> bool:
        try:
            self._queue.put_nowait(_SendJob(send, channel_id))
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning("Chat send queue full; dropped a message for channel %s", channel_id)
            return False
        return True

    @staticmethod
    def _retry_after(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get("Retry-After")
        try:
            return max(0.0, float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def _execute(self, job: _SendJob) -> None:
        for attempt in range(self._max_retries + 1):
            try:
                await job.send()
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status != 429 or attempt == self._max_retries:
                    log.warning(
                        "Chat send failed in channel %s (%s)",
                        job.channel_id,
                        type(exc).__name__,
                    )
                    return
                delay = self._retry_after(exc)
                if delay is None:
                    delay = self._backoff_base * (2**attempt)
                self.retried += 1
                log.info(
                    "Rate limited in channel %s; retrying in %.1fs",
                    job.channel_id,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                self.sent += 1
                return

    async def _run(self) -> None:
        while True:
            timeout = 0.05 if self._stopping.is_set() else max(self._min_interval, 0.05)
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                job = None

            if job is None:
                if self._stopping.is_set() and self._queue.empty():
                    break
                continue

            await self._execute(job)
            if self._min_interval:
                await asyncio.sleep(self._min_interval)
