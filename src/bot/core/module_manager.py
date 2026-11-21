"""Discovery and lifecycle management for modules.

Discovery scans the ``bot.modules`` package for ``Module`` subclasses. Only
modules listed in ``settings.enabled_modules`` are loaded. Initialization
failures are logged and skipped so a single broken module cannot prevent the
application from starting.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from collections.abc import Mapping

import bot.modules as modules_package
from bot.config import Settings
from bot.core.event_router import EventRouter
from bot.modules.base import Module, ModuleContext
from bot.services import Services

log = logging.getLogger(__name__)


class ModuleManager:
    def __init__(self, settings: Settings, router: EventRouter, services: Services) -> None:
        self.settings = settings
        self.router = router
        self.services = services
        self.modules: dict[str, Module] = {}

    def discover(self) -> dict[str, type[Module]]:
        """Return ``{name: class}`` for every discoverable module.

        A feature may be a single module file or a package whose class lives
        in a submodule, so packages are walked recursively.
        """
        found: dict[str, type[Module]] = {}
        for module_info in pkgutil.iter_modules(modules_package.__path__):
            package_name = f"{modules_package.__name__}.{module_info.name}"
            candidates = [importlib.import_module(package_name)]
            if module_info.ispkg:
                candidates.extend(
                    importlib.import_module(child.name)
                    for child in pkgutil.walk_packages(
                        candidates[0].__path__, prefix=f"{package_name}."
                    )
                )

            for candidate in candidates:
                for _, obj in inspect.getmembers(candidate, inspect.isclass):
                    if not (
                        issubclass(obj, Module)
                        and obj is not Module
                        and obj.__module__ == candidate.__name__
                        and obj.name
                    ):
                        continue
                    if obj.name in found and found[obj.name] is not obj:
                        log.warning("Duplicate module name %s; keeping first", obj.name)
                        continue
                    found[obj.name] = obj
        return found

    def _build_context(self, name: str) -> ModuleContext:
        module_settings: Mapping[str, object] = self.settings.module.get(name, {})
        return ModuleContext(
            settings=self.settings,
            config=dict(module_settings),
            router=self.router,
            services=self.services,
            log=logging.getLogger(f"bot.module.{name}"),
        )

    async def load(self) -> None:
        """Discover and initialize every enabled module."""
        available = self.discover()
        log.info("Discovered modules: %s", sorted(available) or "none")

        for name in sorted(available):
            if name not in self.settings.enabled_modules:
                log.info("Module %s is disabled; skipping", name)
                continue

            module_class = available[name]
            try:
                module = module_class(self._build_context(name))
                await module.setup()
            except Exception:
                log.exception("Failed to initialize module %s; skipping", name)
                continue

            self.modules[name] = module
            log.info("Loaded module: %s", name)

    async def start_all(self) -> None:
        for name, module in self.modules.items():
            try:
                await module.start()
                log.info("Started module: %s", name)
            except Exception:
                log.exception("Failed to start module %s", name)

    async def stop_all(self) -> None:
        for name, module in reversed(list(self.modules.items())):
            try:
                await module.stop()
                log.info("Stopped module: %s", name)
            except Exception:
                log.exception("Failed to stop module %s", name)
        self.modules.clear()
