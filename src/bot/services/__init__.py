"""Shared services available to every module via ``ModuleContext``.

Kept dependency-free (typed as ``Any``) so this module can never create a
circular import. Concrete services live in the sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Services:
    database: Any = None
    twitch: Any = None
    llm: Any = None
    permissions: Any = None
