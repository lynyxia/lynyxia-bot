"""Centralized asynchronous database service.

Owns the engine and session factory. Modules receive this service through
their context and must never build their own engine/connections.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import DatabaseSettings

log = logging.getLogger(__name__)


class DatabaseService:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings
        self._engine = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        """Create the pool and wait until the database accepts connections.

        Retrying here keeps startup resilient when Compose launches the bot at
        the same time as PostgreSQL.
        """
        from sqlalchemy.ext.asyncio import AsyncEngine

        engine: AsyncEngine = create_async_engine(
            self.settings.dsn,
            pool_size=self.settings.pool_size,
            max_overflow=self.settings.max_overflow,
            pool_pre_ping=True,
        )

        last_error: Exception | None = None
        for attempt in range(1, self.settings.connect_retries + 1):
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Database not ready (attempt %s/%s): %s",
                    attempt,
                    self.settings.connect_retries,
                    type(exc).__name__,
                )
                if attempt == self.settings.connect_retries:
                    break
                await asyncio.sleep(self.settings.connect_retry_delay)
        else:
            await engine.dispose()
            raise RuntimeError("Could not connect to PostgreSQL") from last_error

        if last_error is not None:
            # The final attempt failed but the loop reached its natural end.
            await engine.dispose()
            raise RuntimeError("Could not connect to PostgreSQL") from last_error

        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        log.info("Database connection established")

    def session(self) -> AsyncSession:
        """Return a session; use it as ``async with db.session() as session``."""
        if self._sessions is None:
            raise RuntimeError("DatabaseService.connect() must be called first")
        return self._sessions()

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessions = None
            log.info("Database connection closed")

    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        """Convenience async context manager with commit/rollback handling."""
        async with self.session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
