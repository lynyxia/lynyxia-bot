"""Import every model here so ``Base.metadata`` is complete for Alembic."""

from bot.database.models.base import Base
from bot.database.models.chat_message import ChatMessage
from bot.database.models.twitch import Channel, User

__all__ = ["Base", "Channel", "ChatMessage", "User"]
