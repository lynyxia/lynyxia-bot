from datetime import UTC, datetime, timedelta, timezone

from bot.utils.time import as_utc


def test_none_passthrough() -> None:
    assert as_utc(None) is None


def test_naive_datetime_is_treated_as_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)
    result = as_utc(naive)
    assert result is not None
    assert result.tzinfo is UTC
    assert result.hour == 12


def test_aware_datetime_is_converted_to_utc() -> None:
    offset = timezone(timedelta(hours=-5))
    aware = datetime(2026, 1, 1, 7, 0, 0, tzinfo=offset)
    result = as_utc(aware)
    assert result is not None
    assert result.utcoffset() == timedelta(0)
    assert result.hour == 12
