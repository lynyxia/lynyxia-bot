"""Module package.

Each subpackage may define exactly one ``Module`` subclass. Discovery is
handled by ``bot.core.module_manager``.
"""

from bot.modules.base import Module, ModuleContext

__all__ = ["Module", "ModuleContext"]
