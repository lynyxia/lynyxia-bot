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
