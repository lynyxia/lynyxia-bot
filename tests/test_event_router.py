from bot.core.event_router import CommandContext, EventRouter


def make_context(command: str = "test") -> CommandContext:
    async def reply(_text: str) -> None:
        return None

    return CommandContext(
        command=command,
        args=["arg"],
        author_id="42",
        author_name="chatter",
        channel_id="7",
        channel_name="channel",
        content=f"!{command} arg",
        reply=reply,
    )


async def test_command_registration_and_dispatch() -> None:
    router = EventRouter()
    seen: list[str] = []

    async def handler(ctx: CommandContext) -> None:
        seen.append(ctx.args[0])

    router.on_command("!Test", handler)
    await router.dispatch_command(make_context("test"))

    assert seen == ["arg"]


async def test_command_names_are_normalized() -> None:
    router = EventRouter()
    seen: list[str] = []

    async def handler(ctx: CommandContext) -> None:
        seen.append(ctx.command)

    router.on_command("CHAT", handler)
    await router.dispatch_command(make_context("chat"))
    assert seen == ["chat"]


async def test_handler_errors_do_not_stop_dispatch() -> None:
    router = EventRouter()
    seen: list[int] = []

    async def broken(ctx: CommandContext) -> None:
        raise RuntimeError("boom")

    async def healthy(ctx: CommandContext) -> None:
        seen.append(1)

    router.on_command("x", broken)
    router.on_command("x", healthy)
    await router.dispatch_command(make_context("x"))
    assert seen == [1]


async def test_subscription_can_be_removed() -> None:
    router = EventRouter()
    seen: list[int] = []

    async def handler(ctx: CommandContext) -> None:
        seen.append(1)

    subscription = router.on_command("x", handler)
    await router.dispatch_command(make_context("x"))
    subscription.unsubscribe()
    await router.dispatch_command(make_context("x"))
    assert seen == [1]


async def test_event_dispatch_forwards_arguments() -> None:
    router = EventRouter()
    seen: list[tuple[str, int]] = []

    async def handler(name: str, count: int) -> None:
        seen.append((name, count))

    router.on_event("Ready", handler)
    await router.dispatch_event("ready", "bot", 3)
    assert seen == [("bot", 3)]
