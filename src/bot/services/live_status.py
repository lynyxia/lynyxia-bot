"""In-memory, per-channel stream status.

EventSub ``stream.online`` / ``stream.offline`` notifications mutate this
cache in real time. Because EventSub only reports transitions, the state is
hydrated once at startup from Helix. A channel with no known state is treated
as offline so a failed lookup never locks users out of the bot.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

log = logging.getLogger(__name__)


class LiveStatusService:
    def __init__(self) -> None:
        self._live: dict[str, bool] = {}

    def is_live(self, channel_id: str) -> bool:
        return bool(self._live.get(channel_id, False))

    def set_live(self, channel_id: str, live: bool) -> None:
        self._live[channel_id] = bool(live)
        log.info(
            "Channel %s is now %s",
            channel_id,
            "live" if live else "offline",
        )

    async def hydrate(self, http: Any, channel_ids: Sequence[str]) -> None:
        """Fetch current stream state once; fail open on any error."""
        if not channel_ids:
            return
        try:
            live_ids: set[str] = set()
            async for stream in http.get_streams(user_ids=list(channel_ids)):
                user = getattr(stream, "user", None)
                streamer_id = str(getattr(user, "id", "") or "")
                if streamer_id:
                    live_ids.add(streamer_id)
            for channel_id in channel_ids:
                self._live[channel_id] = channel_id in live_ids
            log.info(
                "Hydrated live status for %s channel(s): %s live",
                len(channel_ids),
                len(live_ids),
            )
        except Exception:
            log.exception("Could not hydrate stream status; treating channels as offline")
