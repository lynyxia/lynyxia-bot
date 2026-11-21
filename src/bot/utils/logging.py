"""Logging configuration.

Only log levels and non-sensitive context are configured here; secrets are
never accepted as log arguments by the rest of the application.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Third-party libraries should not flood the log at DEBUG by default.
    logging.getLogger("asyncio").setLevel(max(numeric_level, logging.WARNING))
