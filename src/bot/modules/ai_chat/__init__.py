"""AI chat module — foundation stub.

``!chat`` and ``!chatsettings`` commands will be implemented in a later
milestone using the ``LLMProvider`` interface and per-module configuration.
"""

from bot.modules.base import Module


class AIChatModule(Module):
    name = "ai_chat"
