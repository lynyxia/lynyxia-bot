"""Chat logger module — foundation stub.

Persistence of chat messages will be implemented in a later milestone using
``ChatMessageRepository`` and a batched, non-blocking writer.
"""

from bot.modules.base import Module


class ChatLoggerModule(Module):
    name = "chat_logger"
