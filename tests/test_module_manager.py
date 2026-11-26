from bot.core.event_router import CommandContext, EventRouter
from bot.core.module_manager import ModuleManager
from bot.modules import Module
from bot.modules.quotes import QuotesModule
from bot.services import Services


def make_manager(settings) -> ModuleManager:
    return ModuleManager(settings, EventRouter(), Services())


def make_context() -> CommandContext:
    async def reply(_text: str) -> None:
        return None

    return CommandContext(
        command="ping",
        args=[],
        author_id="1",
        author_name="u",
        channel_id="2",
        channel_name="c",
        content="!ping",
        reply=reply,
    )


def test_discovery_finds_foundation_modules(settings) -> None:
    assert set(make_manager(settings).discover()) == {
        "chat_logger",
        "quotes",
        "ecount",
        "lines",
        "ai_chat",
        "lynyxia",
        "livepass",
        "trivia",
        "pyramid",
        "unused_emotes",
        "emote_sets",
    }


async def test_disabled_modules_are_not_loaded(settings) -> None:
    settings.enabled_modules = ["quotes"]
    manager = make_manager(settings)
    await manager.load()
    assert list(manager.modules) == ["quotes"]
    await manager.stop_all()


async def test_initialization_failure_is_isolated(settings, monkeypatch, caplog) -> None:
    class Broken(Module):
        name = "broken"

        async def setup(self) -> None:
            raise RuntimeError("bad configuration")

    settings.enabled_modules = ["broken", "quotes"]
    manager = make_manager(settings)
    monkeypatch.setattr(
        manager, "discover", lambda: {"broken": Broken, "quotes": QuotesModule}
    )

    with caplog.at_level("ERROR"):
        await manager.load()

    assert list(manager.modules) == ["quotes"]
    assert "Failed to initialize module broken" in caplog.text
    await manager.stop_all()


async def test_stop_unregisters_module_handlers(settings, monkeypatch) -> None:
    seen: list[int] = []

    class Registered(Module):
        name = "registered"

        async def setup(self) -> None:
            async def handler(_ctx: CommandContext) -> None:
                seen.append(1)

            self.subscribe(self.context.router.on_command("ping", handler))

    settings.enabled_modules = ["registered"]
    manager = make_manager(settings)
    monkeypatch.setattr(manager, "discover", lambda: {"registered": Registered})

    await manager.load()
    await manager.start_all()
    await manager.router.dispatch_command(make_context())
    assert seen == [1]

    await manager.stop_all()
    await manager.router.dispatch_command(make_context())
    assert seen == [1]
