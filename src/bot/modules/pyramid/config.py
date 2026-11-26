"""Configuration for the pyramid watcher."""

from __future__ import annotations

from pydantic import BaseModel


class PyramidConfig(BaseModel):
    # Quick kill switch; the module can also be removed from BOT_ENABLED_MODULES.
    enabled: bool = True
