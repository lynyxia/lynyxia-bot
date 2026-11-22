import asyncio

from bot.core.application import Application


async def test_application_lifecycle(settings, fake_database, fake_bot) -> None:
    app = Application(settings, bot=fake_bot, database=fake_database)

    await app.start()
    assert fake_database.connected
    assert set(app.modules.modules) == {
        "chat_logger",
        "quotes",
        "ecount",
        "lines",
        "ai_chat",
    }
    assert fake_bot.router is app.router
    assert app.twitch._client is fake_bot

    await asyncio.sleep(0)
    assert fake_bot.started
    assert app.chat_queue._worker is not None
    assert fake_bot.start_args[0] == ()
    assert fake_bot.start_args[1] == {
        "with_adapter": True,
        "load_tokens": True,
        "save_tokens": True,
    }

    await app.stop()
    assert fake_bot.closed
    assert fake_database.disposed
    assert app.modules.modules == {}
    assert app.bot_task is None
    assert app.chat_queue._worker is None


async def test_application_context_manager(settings, fake_database, fake_bot) -> None:
    async with Application(settings, bot=fake_bot, database=fake_database) as app:
        assert fake_database.connected
        assert app.router is not None
    assert fake_database.disposed
