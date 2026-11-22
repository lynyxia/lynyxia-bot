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

    async def send(content: str) -> None:
        calls.append(content)

    await DirectChatSender().submit(send, "hello", "7")
    assert calls == ["hello"]


async def test_queue_orders_and_counts_sends() -> None:
    queue = ChatSendQueue(min_interval_seconds=0, queue_maxsize=10)
    calls: list[int] = []
    done = asyncio.Event()

    def make_job(index: int):
        async def job(_content: str) -> None:
            calls.append(index)
            if len(calls) == 3:
                done.set()

        return job

    await queue.start()
    for index in range(3):
        assert await queue.submit(make_job(index), f"message {index}", "7")
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

    async def job(_content: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RateLimited()

    await queue.start()
    assert await queue.submit(job, "hello", "7")
    await wait_until(lambda: queue.sent == 1)
    await queue.stop()

    assert len(calls) == 2
    assert queue.retried == 1


async def test_queue_gives_up_after_max_retries() -> None:
    queue = ChatSendQueue(
        min_interval_seconds=0, max_retries=1, backoff_base_seconds=0
    )
    calls: list[int] = []

    async def job(_content: str) -> None:
        calls.append(1)
        raise RateLimited()

    await queue.start()
    assert await queue.submit(job, "hello", "7")
    await wait_until(lambda: queue.retried == 1)
    await queue.stop()

    assert len(calls) == 2
    assert queue.sent == 0


async def test_overflow_drops_instead_of_growing() -> None:
    queue = ChatSendQueue(queue_maxsize=1)

    async def job(_content: str) -> None:
        return None

    assert await queue.submit(job, "hello", "7")
    assert not await queue.submit(job, "hello", "7")
    assert queue.dropped == 1


async def test_stop_drains_pending_sends() -> None:
    queue = ChatSendQueue(min_interval_seconds=0)
    calls: list[str] = []

    async def job(content: str) -> None:
        calls.append(content)

    assert await queue.submit(job, "hello", "7")
    await queue.start()
    await queue.stop()

    assert calls == ["hello"]
    assert queue.sent == 1


async def test_banned_phrase_replaces_message_and_logs(caplog) -> None:
    replacement = "[System] Blocklisted phrase detected in reply... Aborting."
    queue = ChatSendQueue(
        min_interval_seconds=0,
        banned_phrases=["Racial Slur"],
        banned_phrase_replacement=replacement,
    )
    sent: list[str] = []

    async def job(content: str) -> None:
        sent.append(content)

    original = "Kappa123: that is a RACIAL SLUR, mods please"
    assert await queue.submit(job, original, "7")
    await queue.start()
    await queue.stop()

    assert sent == [replacement]
    assert f"banned phrase detected in: {original}" in caplog.text


async def test_banned_phrase_check_is_case_insensitive_and_clean_phrases(caplog) -> None:
    queue = ChatSendQueue(
        min_interval_seconds=0,
        banned_phrases=["  bad word  ", ""],
        banned_phrase_replacement="[filtered]",
    )
    sent: list[str] = []

    async def job(content: str) -> None:
        sent.append(content)

    await queue.start()
    assert await queue.submit(job, "this has a BAD WORD here", "7")
    assert await queue.submit(job, "this is fine", "7")
    await queue.stop()

    assert sent == ["[filtered]", "this is fine"]
    assert caplog.text.count("banned phrase detected in:") == 1
