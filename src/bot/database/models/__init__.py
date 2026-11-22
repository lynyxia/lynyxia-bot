"""Import every model here so ``Base.metadata`` is complete for Alembic."""

from bot.database.models.ai_chat import AiChatAllowlist, AiChatChannel, AiChatThread
from bot.database.models.base import Base
from bot.database.models.channel_authorization import ChannelAuthorization
from bot.database.models.channel_state import ChannelState
from bot.database.models.chat_message import ChatMessage
from bot.database.models.oauth_token import OAuthToken
from bot.database.models.twitch import Channel, User

__all__ = [
    "AiChatAllowlist",
    "AiChatChannel",
    "AiChatThread",
    "Base",
    "Channel",
    "ChannelAuthorization",
    "ChannelState",
    "ChatMessage",
    "OAuthToken",
    "User",
]
