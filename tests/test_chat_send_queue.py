"""Tests for the shared outbound chat send queue."""

from __future__ import annotations

import asyncio

from bot.core.chat_send_queue import ChatSendQueue, DirectChatSender


class RateLimited(Exception):
    status = 429


async def wait_until(predicate, timeout: float = 1) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout)


async def test_direct_sender_runs_immediately() -> None:
    calls: list[str] = []

    async def send() -> None:
        calls.append("sent")

    await DirectChatSender().submit(send, "7")
    assert calls == ["sent"]


async def test_queue_orders_and_counts_sends() -> None:
    queue = ChatSendQueue(min_interval_seconds=0, queue_maxsize=10)
    calls: list[int] = []
    done = asyncio.Event()

    def make_job(index: int):
        async def job() -> None:
            calls.append(index)
            if len(calls) == 3:
                done.set()

        return job

    await queue.start()
    for index in range(3):
        assert await queue.submit(make_job(index), "7")
    await asyncio.wait_for(done.wait(), timeout=1)
    await queue.stop()

    assert calls == [0, 1, 2]
    assert queue.sent == 3
    assert queue.dropped == 0


async def test_queue_retries_rate_limited_send() -> None:
    queue = ChatSendQueue(
        min_interval_seconds=0, max_retries=2, backoff_base_seconds=0
    )
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RateLimited()

    await queue.start()
    assert await queue.submit(job, "7")
    await wait_until(lambda: queue.sent == 1)
    await queue.stop()

    assert len(calls) == 2
    assert queue.retried == 1


async def test_queue_gives_up_after_max_retries() -> None:
    queue = ChatSendQueue(
        min_interval_seconds=0, max_retries=1, backoff_base_seconds=0
    )
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)
        raise RateLimited()

    await queue.start()
    assert await queue.submit(job, "7")
    await wait_until(lambda: queue.retried == 1)
    await queue.stop()

    assert len(calls) == 2
    assert queue.sent == 0


async def test_overflow_drops_instead_of_growing() -> None:
    queue = ChatSendQueue(queue_maxsize=1)

    async def job() -> None:
        return None

    assert await queue.submit(job, "7")
    assert not await queue.submit(job, "7")
    assert queue.dropped == 1


async def test_stop_drains_pending_sends() -> None:
    queue = ChatSendQueue(min_interval_seconds=0)
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)

    assert await queue.submit(job, "7")
    await queue.start()
    await queue.stop()

    assert calls == [1]
    assert queue.sent == 1
