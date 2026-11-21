"""Base repository: all data access goes through the shared database service."""

from __future__ import annotations

from bot.services.database import DatabaseService


class BaseRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database
