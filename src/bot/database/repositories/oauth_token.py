"""Persistence for the bot's OAuth token pair."""

from __future__ import annotations

from bot.database.models import OAuthToken
from bot.database.repositories.base import BaseRepository


class OAuthTokenRepository(BaseRepository):
    async def get(self, user_id: str) -> OAuthToken | None:
        async with self.database.session() as session:
            return await session.get(OAuthToken, user_id)

    async def save(
        self,
        user_id: str,
        access_token: str,
        refresh_token: str,
        scopes: str = "",
    ) -> None:
        async with self.database.session() as session:
            token = await session.get(OAuthToken, user_id)
            if token is None:
                token = OAuthToken(user_id=user_id)
                session.add(token)
            token.access_token = access_token
            token.refresh_token = refresh_token
            token.scopes = scopes
            await session.commit()

    async def delete(self, user_id: str) -> None:
        async with self.database.session() as session:
            token = await session.get(OAuthToken, user_id)
            if token is not None:
                await session.delete(token)
                await session.commit()
