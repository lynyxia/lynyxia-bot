"""The contract every module implements.

A module receives a ``ModuleContext`` and may register handlers in ``setup()``.
Subscriptions are tracked by the base class and automatically removed in
``stop()`` so disabled/stopped modules leave no handlers behind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from bot.config import Settings
from bot.core.event_router import EventRouter, Subscription
from bot.services import Services


@dataclass
class ModuleContext:
    settings: Settings
    config: dict[str, Any]
    router: EventRouter
    services: Services
    log: logging.Logger


class Module:
    """Base class for all modules.

    Override ``name`` with a unique identifier used in ``enabled_modules``.
    """

    name: ClassVar[str] = ""

    def __init__(self, context: ModuleContext) -> None:
        self.context = context
        self.log = context.log
        self._subscriptions: list[Subscription] = []

    async def setup(self) -> None:
        """Register handlers and validate module configuration."""

    async def start(self) -> None:
        """Begin background work after all modules have been set up."""

    async def stop(self) -> None:
        """Release resources and unregister all handlers."""
        for subscription in reversed(self._subscriptions):
            subscription.unsubscribe()
        self._subscriptions.clear()

    def subscribe(self, subscription: Subscription) -> Subscription:
        """Track a router subscription so it is cleaned up on ``stop()``."""
        self._subscriptions.append(subscription)
        return subscription
