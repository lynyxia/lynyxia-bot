from unittest.mock import AsyncMock, Mock

import pytest

from bot.services.twitch import TwitchService


async def test_send_message_uses_twitchio3_api() -> None:
    service = TwitchService()
    channel = Mock()
    channel.send_message = AsyncMock()
    client = Mock()
    client.bot_id = "42"
    client.create_partialuser = Mock(return_value=channel)
    service.bind(client)

    await service.send_message("7", "hello")

    client.create_partialuser.assert_called_once_with("7")
    channel.send_message.assert_awaited_once_with("hello", sender="42")


async def test_send_message_requires_bound_client() -> None:
    service = TwitchService()
    with pytest.raises(RuntimeError, match="not available"):
        await service.send_message("7", "hello")


class Unauthorized(Exception):
    status = 401


class RateLimited(Exception):
    status = 429


def make_client(token_ids: tuple[str, ...] = ("42",)) -> tuple[Mock, AsyncMock]:
    channel = Mock()
    channel.send_message = AsyncMock()
    client = Mock()
    client.bot_id = "42"
    client.tokens = {token_id: object() for token_id in token_ids}
    client.create_partialuser = Mock(return_value=channel)
    client.channel_authorization_url = Mock(return_value="https://auth.example/channel")
    client._login_for = Mock(return_value="testchannel")
    return client, channel.send_message


async def test_send_message_falls_back_to_user_token_on_401() -> None:
    service = TwitchService()
    client, send = make_client()
    send.side_effect = [Unauthorized(), None]
    service.bind(client)

    await service.send_message("7", "hello")

    assert send.await_count == 2
    assert "token_for" not in send.await_args_list[0].kwargs
    assert send.await_args_list[1].kwargs["token_for"] == "42"


async def test_send_message_without_user_token_does_not_retry(caplog) -> None:
    service = TwitchService()
    client, send = make_client(token_ids=())
    send.side_effect = Unauthorized()
    service.bind(client)

    await service.send_message("7", "hello")

    assert send.await_count == 1
    assert "have the broadcaster authorize" in caplog.text
    assert "https://auth.example/channel" in caplog.text


async def test_send_message_propagates_rate_limit_to_shared_queue() -> None:
    service = TwitchService()
    client, send = make_client()
    send.side_effect = RateLimited()
    service.bind(client)

    # The queue needs to see 429 so it can apply its own retry/backoff.
    with pytest.raises(RateLimited):
        await service.send_message("7", "hello")
