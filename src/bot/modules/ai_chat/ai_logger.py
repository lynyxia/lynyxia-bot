"""Dedicated rotating logger for AI chat requests, reasoning and responses."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class AILogger:
    def __init__(self, path: str) -> None:
        self.log = logging.getLogger("bot.ai_chat.ai")
        self.log.handlers.clear()
        self.log.propagate = False
        self.log.setLevel(logging.INFO)

        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(_FORMAT))
        self.log.addHandler(handler)

    def request(self, messages: object) -> None:
        self.log.info("REQUEST %s", messages)

    def reasoning(self, text: str | None) -> None:
        if text:
            self.log.info("THINKING %s", text)

    def tool(self, name: str, arguments: object, result: str) -> None:
        self.log.info("TOOL %s args=%s result=%s", name, arguments, result)

    def response(self, text: str | None) -> None:
        self.log.info("RESPONSE %s", text)

    def error(self, message: str) -> None:
        self.log.error("ERROR %s", message)

    def close(self) -> None:
        for handler in list(self.log.handlers):
            handler.close()
            self.log.removeHandler(handler)
