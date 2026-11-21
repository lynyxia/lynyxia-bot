"""Application entry point."""

from __future__ import annotations

import asyncio
import signal

from bot.config import Settings
from bot.core.application import Application


async def main() -> None:
    settings = Settings()
    app = Application(settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    handled = (signal.SIGINT, signal.SIGTERM)
    for sig in handled:
        loop.add_signal_handler(sig, stop_event.set)

    await app.start()
    try:
        bot_task = app.bot_task
        waiters = [asyncio.create_task(stop_event.wait())]
        if bot_task is not None:
            waiters.append(bot_task)  # type: ignore[arg-type]
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for sig in handled:
            loop.remove_signal_handler(sig)
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
