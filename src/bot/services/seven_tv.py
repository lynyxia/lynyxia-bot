"""Small async client for the 7TV v3 API used by the report modules."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

HEADERS = {"Client-ID": "*", "Accept": "application/json"}
RETRIES = 3


class SevenTVError(RuntimeError):
    """Raised when 7TV cannot be reached or returns unusable data."""


class SevenTVClient:
    def __init__(
        self, base_url: str, timeout: float, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def _get_json(self, path: str) -> object:
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            for attempt in range(RETRIES):
                response = await client.get(f"{self._base}{path}", headers=HEADERS)
                if response.status_code == 429 and attempt < RETRIES - 1:
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SevenTVError(str(exc)) from exc
        finally:
            if self._owns_client:
                await client.aclose()
        raise SevenTVError("7TV request failed")

    async def emote_set_id(self, twitch_id: str) -> str | None:
        """Return the user's personal emote set ID, if they have one."""
        data = await self._get_json(f"/users/twitch/{quote(twitch_id, safe='')}")
        value = data.get("emote_set_id") if isinstance(data, dict) else None
        return str(value) if value else None

    async def owned_emote_sets(self, twitch_id: str) -> list[dict]:
        """Return ``{id, name}`` for the emote sets the channel owns."""
        data = await self._get_json(f"/users/twitch/{quote(twitch_id, safe='')}")
        if not isinstance(data, dict):
            return []
        user = data.get("user", data)
        entries = user.get("emote_sets") if isinstance(user, dict) else None
        if not isinstance(entries, list):
            return []
        sets: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            set_id = str(entry.get("id") or "")
            name = str(entry.get("name") or "")
            if set_id and name:
                sets.append({"id": set_id, "name": name})
        return sets

    async def emote_set(self, emote_set_id: str) -> dict:
        """Return a normalized ``{id, name, emotes}`` object for one set."""
        data = await self._get_json(
            f"/emote-sets/{quote(emote_set_id, safe='')}"
        )
        if not isinstance(data, dict):
            return {}
        # Some payloads nest the set under "emote_set"; accept both shapes.
        nested = data.get("emote_set")
        if isinstance(nested, dict):
            data = nested
        emotes = data.get("emotes")
        return {
            "id": str(data.get("id") or emote_set_id),
            "name": str(data.get("name") or ""),
            "emotes": (
                [entry for entry in emotes if isinstance(entry, dict)]
                if isinstance(emotes, list)
                else []
            ),
        }

    async def active_emotes(self, emote_set_id: str) -> list[dict]:
        """Return the emote objects in an emote set."""
        return (await self.emote_set(emote_set_id))["emotes"]
