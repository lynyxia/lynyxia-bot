"""Timezone helpers.

All message timestamps are stored and compared as UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as a timezone-aware UTC datetime.

    Naive inputs are interpreted as UTC (Twitch supplies UTC timestamps without
    an offset); aware inputs are converted with ``astimezone``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
