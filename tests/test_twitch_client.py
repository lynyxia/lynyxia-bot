"""Checks the TwitchIO 3.x EventSub and OAuth integration."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

from bot.core.bot import BotClient
from bot.core.event_router import ChatMessageEvent, CommandContext, EventRouter


class StubTokenStore:
    def __init__(self, record: object | None = None) -> None:
        self.record = record
        self.saved: list[tuple[str, str, str, str]] = []

    async def get(self, user_id: str) -> object | None:
        return self.record

    async def save(
        self, user_id: str, access_token: str, refresh_token: str, scopes: str = ""
    ) -> None:
        self.saved.append((user_id, access_token, refresh_token, scopes))


class StubChannelStore:
    def __init__(self, records: Sequence[object] = ()) -> None:
        self.records = list(records)
        self.saved: list[tuple[str, str, str, str, str]] = []

    async def list_all(self):
        return self.records

    async def save(
        self,
        channel_id: str,
        login: str,
        access_token: str,
        refresh_token: str,
        scopes: str = "",
    ) -> None:
        self.saved.append((channel_id, login, access_token, refresh_token, scopes))


class Validated:
    user_id = "42"


class Payload:
    def __init__(self, **values: object) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def make_bot(
    settings,
    router: EventRouter | None = None,
    token_store: StubTokenStore | None = None,
    channel_store: StubChannelStore | None = None,
) -> BotClient:
    return BotClient(
        router=router or EventRouter(),
        client_id=settings.twitch.client_id,
        client_secret=settings.twitch.client_secret.get_secret_value(),
        bot_id=settings.twitch.bot_id,
        channel_ids=settings.twitch.channel_id,
        channel_logins=settings.twitch.channel,
        redirect_uri=settings.twitch.oauth_redirect_uri,
        scopes=settings.twitch.oauth_scopes,
        channel_scopes=settings.twitch.channel_scopes,
        token_store=token_store or StubTokenStore(),
        channel_token_store=channel_store or StubChannelStore(),
        prefix="!",
    )


def make_context(command: str) -> CommandContext:
    async def reply(_text: str) -> None:
        return None

    return CommandContext(
        command=command,
        args=[],
        author_id="1",
        author_name="u",
        channel_id="2",
        channel_name="c",
        content=f"!{command}",
        reply=reply,
    )


def stored_token(bot: BotClient, user_id: str = "42") -> None:
    bot._http._tokens[user_id] = {
        "user_id": user_id,
        "token": "stored-access",
        "refresh": "stored-refresh",
        "last_validated": "2026-01-01T00:00:00",
    }


def test_bot_client_accepts_client_id_and_secret(settings) -> None:
    bot = make_bot(settings)
    assert bot._http.client_id == "test-client-id"
    assert bot._http.client_secret == "test-client-secret"
    assert bot.bot_id == "42"


def test_authorization_url_contains_required_parameters(settings) -> None:
    url = make_bot(settings).authorization_url
    assert "client_id=test-client-id" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Foauth%2Fcallback" in url
    assert "user%3Aread%3Achat" in url
    assert "response_type=code" in url


async def test_load_tokens_registers_stored_token(settings) -> None:
    record = Payload(access_token="access", refresh_token="refresh")
    bot = make_bot(settings, token_store=StubTokenStore(record))
    bot.add_token = AsyncMock(return_value=Validated())  # type: ignore[method-assign]

    await bot.load_tokens()

    bot.add_token.assert_awaited_once_with("access", "refresh")


async def test_load_tokens_without_stored_token_is_noop(settings) -> None:
    bot = make_bot(settings)
    bot.add_token = AsyncMock()  # type: ignore[method-assign]
    await bot.load_tokens()
    bot.add_token.assert_not_awaited()


async def test_invalid_stored_token_does_not_break_startup(settings, caplog) -> None:
    record = Payload(access_token="bad", refresh_token="bad")
    bot = make_bot(settings, token_store=StubTokenStore(record))
    bot.add_token = AsyncMock(side_effect=RuntimeError("SECRET-TOKEN"))  # type: ignore[method-assign]

    await bot.load_tokens()

    assert "SECRET-TOKEN" not in caplog.text
    assert "authorization is required" in caplog.text


async def test_setup_hook_subscribes_when_token_exists(settings) -> None:
    bot = make_bot(settings)
    stored_token(bot)
    bot.subscribe_websocket = AsyncMock()  # type: ignore[method-assign]

    await bot.setup_hook()

    conditions = [call.args[0].condition for call in bot.subscribe_websocket.await_args_list]
    assert conditions == [
        {"broadcaster_user_id": "7", "user_id": "42"},
    ]


async def test_setup_hook_subscribes_to_every_channel(settings) -> None:
    settings.twitch.channel = ["a", "b"]
    settings.twitch.channel_id = ["7", "8"]
    bot = make_bot(settings)
    stored_token(bot)
    bot.subscribe_websocket = AsyncMock()  # type: ignore[method-assign]

    await bot.setup_hook()

    conditions = [call.args[0].condition for call in bot.subscribe_websocket.await_args_list]
    assert conditions == [
        {"broadcaster_user_id": "7", "user_id": "42"},
        {"broadcaster_user_id": "8", "user_id": "42"},
    ]


async def test_setup_hook_requests_authorization_when_no_token(settings, caplog) -> None:
    bot = make_bot(settings)
    bot.subscribe_websocket = AsyncMock()  # type: ignore[method-assign]

    await bot.setup_hook()

    assert "Twitch authorization required" in caplog.text
    bot.subscribe_websocket.assert_not_awaited()


async def test_oauth_callback_registers_and_persists_token(settings) -> None:
    store = StubTokenStore()
    bot = make_bot(settings, token_store=store)
    stored_token(bot)
    bot.add_token = AsyncMock(return_value=Validated())  # type: ignore[method-assign]
    bot.subscribe_websocket = AsyncMock()  # type: ignore[method-assign]

    await bot.event_oauth_authorized(
        Payload(user_id="42", access_token="new-access", refresh_token="new-refresh")
    )

    bot.add_token.assert_awaited_once_with("new-access", "new-refresh")
    assert store.saved[0][1:3] == ("stored-access", "stored-refresh")
    bot.subscribe_websocket.assert_awaited_once()


async def test_oauth_callback_rejects_wrong_user(settings) -> None:
    bot = make_bot(settings)
    bot.add_token = AsyncMock()  # type: ignore[method-assign]

    await bot.event_oauth_authorized(
        Payload(user_id="999", access_token="access", refresh_token="refresh")
    )

    bot.add_token.assert_not_awaited()


async def test_save_tokens_persists_rotated_pair(settings) -> None:
    store = StubTokenStore()
    bot = make_bot(settings, token_store=store)
    stored_token(bot)

    await bot.save_tokens()

    assert store.saved[0][1:3] == ("stored-access", "stored-refresh")


async def test_token_refreshed_event_persists(settings) -> None:
    store = StubTokenStore()
    bot = make_bot(settings, token_store=store)
    stored_token(bot)

    await bot.event_token_refreshed(Payload(user_id="42"))

    assert len(store.saved) == 1


async def test_bot_forwards_commands_to_router(settings) -> None:
    router = EventRouter()
    seen: list[str] = []

    async def handler(ctx: CommandContext) -> None:
        seen.append(ctx.command)

    router.on_command("hello", handler)
    bot = make_bot(settings, router)

    class Author:
        id = "99"
        name = "chatter"

    class Channel:
        id = "7"
        name = "channel"

        async def send(self, _text: str) -> None:
            return None

    class Message:
        content = "!hello"
        echo = False
        author = Author()
        channel = Channel()

    await bot.event_message(Message())
    assert seen == ["hello"]


async def test_echo_messages_are_ignored(settings) -> None:
    router = EventRouter()
    seen: list[str] = []

    async def handler(_ctx: CommandContext) -> None:
        seen.append("called")

    router.on_command("hello", handler)
    bot = make_bot(settings, router)

    class Message:
        content = "!hello"
        echo = True

    await bot.event_message(Message())
    assert seen == []


async def test_twitchio3_message_uses_broadcaster_for_channel(settings) -> None:
    router = EventRouter()
    seen: list[CommandContext] = []
    replies: list[str] = []

    async def handler(ctx: CommandContext) -> None:
        seen.append(ctx)
        await ctx.reply("pong")

    router.on_command("ping", handler)
    bot = make_bot(settings, router)

    class User:
        def __init__(self, user_id: str, name: str) -> None:
            self.id = user_id
            self.name = name

    class Message:
        text = "!ping"
        chatter = User("99", "chatter")
        broadcaster = User("8", "otherchannel")

        async def respond(self, content: str) -> None:
            replies.append(content)

    await bot.event_message(Message())

    assert len(seen) == 1
    assert seen[0].channel_id == "8"
    assert seen[0].channel_name == "otherchannel"
    assert seen[0].author_id == "99"
    assert replies == ["pong"]


async def test_message_event_is_normalized_for_modules(settings) -> None:
    router = EventRouter()
    seen: list[ChatMessageEvent] = []

    async def handler(event: ChatMessageEvent) -> None:
        seen.append(event)

    router.on_event("message", handler)
    bot = make_bot(settings, router)

    class User:
        def __init__(self, user_id: str, name: str, display_name: str) -> None:
            self.id = user_id
            self.name = name
            self.display_name = display_name

    class Message:
        text = "hello"
        id = "msg-1"
        timestamp = None
        chatter = User("99", "chatter", "Chatter")
        broadcaster = User("7", "channel", "Channel")

    await bot.event_message(Message())

    assert len(seen) == 1
    event = seen[0]
    assert event.author_id == "99"
    assert event.author_login == "chatter"
    assert event.author_name == "Chatter"
    assert event.channel_id == "7"
    assert event.channel_login == "channel"
    assert event.channel_name == "Channel"
    assert event.content == "hello"
    assert event.message_id == "msg-1"
    assert isinstance(event.raw, Message)


async def test_message_timestamp_is_normalized_to_utc(settings) -> None:
    router = EventRouter()
    seen: list[ChatMessageEvent] = []

    async def handler(event: ChatMessageEvent) -> None:
        seen.append(event)

    router.on_event("message", handler)
    bot = make_bot(settings, router)

    class User:
        id = "99"
        name = "chatter"
        display_name = "Chatter"

    class Message:
        text = "hello"
        timestamp = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=-5)))
        chatter = User()

    await bot.event_message(Message())

    assert seen[0].sent_at is not None
    assert seen[0].sent_at.utcoffset() == timedelta(0)
    assert seen[0].sent_at.hour == 12
    assert seen[0].sent_at.tzinfo is UTC


async def test_threaded_reply_uses_twitch_reply_to_message_id(settings) -> None:
    router = EventRouter()
    replies: list[str] = []

    async def handler(ctx: CommandContext) -> None:
        replies.append(ctx.command)
        await ctx.reply_threaded("threaded answer")

    router.on_command("rq", handler)
    bot = make_bot(settings, router)

    class User:
        def __init__(self, user_id: str, name: str) -> None:
            self.id = user_id
            self.name = name

    class Broadcaster(User):
        def __init__(self) -> None:
            super().__init__("7", "channel")
            self.send_message = AsyncMock()

    broadcaster = Broadcaster()

    class Message:
        text = "!rq Kappa123"
        id = "parent-msg"
        chatter = User("99", "chatter")

        async def respond(self, _content: str) -> None:
            raise AssertionError("standalone respond should not be used")

    Message.broadcaster = broadcaster  # type: ignore[attr-defined]
    await bot.event_message(Message())

    assert replies == ["rq"]
    broadcaster.send_message.assert_awaited_once_with(
        "threaded answer", sender="42", reply_to_message_id="parent-msg"
    )


def test_channel_authorization_url_uses_channel_scopes(settings) -> None:
    url = make_bot(settings).channel_authorization_url()
    assert "scope=channel%3Abot" in url
    assert "client_id=test-client-id" in url


async def test_broadcaster_callback_marks_channel_authorized(settings) -> None:
    store = StubChannelStore()
    bot = make_bot(settings, channel_store=store)
    stored_token(bot, "7")

    class Broadcaster:
        user_id = "7"

    bot.add_token = AsyncMock(return_value=Broadcaster())  # type: ignore[method-assign]

    await bot.event_oauth_authorized(
        Payload(user_id="7", access_token="access", refresh_token="refresh")
    )

    assert "7" in bot._authorized_channels
    assert store.saved[0][:2] == ("7", "testchannel")
    assert store.saved[0][3] == "stored-refresh"


async def test_channel_token_refresh_is_persisted(settings) -> None:
    store = StubChannelStore()
    bot = make_bot(settings, channel_store=store)
    stored_token(bot, "7")
    bot._authorized_channels.add("7")

    await bot.event_token_refreshed(Payload(user_id="7"))

    assert store.saved[0][0] == "7"


async def test_setup_hook_logs_missing_channel_authorization(settings, caplog) -> None:
    bot = make_bot(settings)
    bot.subscribe_websocket = AsyncMock()  # type: ignore[method-assign]

    await bot.setup_hook()

    assert "must authorize the bot" in caplog.text
    assert "testchannel" in caplog.text
    assert "scope=channel%3Abot" in caplog.text


async def test_unauthorized_send_falls_back_to_user_token(settings, caplog) -> None:
    router = EventRouter()

    async def handler(ctx: CommandContext) -> None:
        await ctx.reply("hello")

    router.on_command("ping", handler)
    bot = make_bot(settings, router)
    stored_token(bot, "42")

    class Unauthorized(Exception):
        status = 401

    class User:
        id = "99"
        name = "chatter"

    send = AsyncMock(side_effect=[Unauthorized(), None])

    class Broadcaster:
        id = "7"
        name = "testchannel"

    class Message:
        text = "!ping"
        id = "msg-1"
        chatter = User()
        broadcaster = Broadcaster()

    Message.broadcaster.send_message = send  # type: ignore[attr-defined]
    await bot.event_message(Message())

    assert send.await_count == 2
    assert "token_for" not in send.await_args_list[0].kwargs
    assert send.await_args_list[1].kwargs["token_for"] == "42"
    assert "using the bot user token" in caplog.text
    assert "scope=channel%3Abot" in caplog.text


async def test_fallback_failure_is_logged(settings, caplog) -> None:
    router = EventRouter()

    async def handler(ctx: CommandContext) -> None:
        await ctx.reply("hello")

    router.on_command("ping", handler)
    bot = make_bot(settings, router)
    stored_token(bot, "42")

    class Unauthorized(Exception):
        status = 401

    class User:
        id = "99"
        name = "chatter"

    send = AsyncMock(side_effect=Unauthorized())

    class Broadcaster:
        id = "7"
        name = "testchannel"

    class Message:
        text = "!ping"
        id = "msg-1"
        chatter = User()
        broadcaster = Broadcaster()

    Message.broadcaster.send_message = send  # type: ignore[attr-defined]
    await bot.event_message(Message())

    assert send.await_count == 2
    assert "Fallback user-token send failed" in caplog.text


async def test_authorized_channel_does_not_fallback(settings) -> None:
    router = EventRouter()

    async def handler(ctx: CommandContext) -> None:
        await ctx.reply("hello")

    router.on_command("ping", handler)
    bot = make_bot(settings, router)
    bot._authorized_channels.add("7")
    send = AsyncMock()

    class User:
        id = "99"
        name = "chatter"

    class Broadcaster:
        id = "7"
        name = "testchannel"

    class Message:
        text = "!ping"
        id = "msg-1"
        chatter = User()
        broadcaster = Broadcaster()

    Message.broadcaster.send_message = send  # type: ignore[attr-defined]
    await bot.event_message(Message())

    assert send.await_count == 1
    assert "token_for" not in send.await_args.kwargs


async def test_bot_ignores_its_own_messages(settings) -> None:
    router = EventRouter()
    seen: list[str] = []

    async def handler(_ctx: CommandContext) -> None:
        seen.append("called")

    router.on_command("ping", handler)
    bot = make_bot(settings, router)

    class User:
        id = "42"
        name = "testbot"

    class Message:
        text = "!ping"
        chatter = User()

    await bot.event_message(Message())
    assert seen == []
