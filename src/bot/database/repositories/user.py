"""Repository for Twitch users."""

from __future__ import annotations

from sqlalchemy import func, select

from bot.database.models import User
from bot.database.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_login(self, login: str) -> User | None:
        """Resolve a current Twitch login (case-insensitively) to a user row."""
        stmt = select(User).where(func.lower(User.login) == login.lower()).limit(1)
        async with self.database.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> User | None:
        """Look up a user by their stable Twitch ID."""
        if not user_id:
            return None
        async with self.database.session() as session:
            return await session.get(User, user_id)
