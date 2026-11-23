"""Times and fixed-point numbers, as both file formats store them.

* Every record's ``time`` is a .NET tick count: 100-nanosecond units since
  0001-01-01 00:00 UTC.
* Prices, sizes and positions are integers scaled by 10**8, exact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Prices, sizes and positions are stored as integers times SCALE.
SCALE = 100_000_000
#: Ticks (100 ns since 0001-01-01 UTC) at 1970-01-01 00:00 UTC.
TICKS_AT_UNIX_EPOCH = 621_355_968_000_000_000

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def to_float(value: int) -> float:
    """A scaled integer (price, size, position) as a float. Keep the integer for exact sums."""
    return value / SCALE


def ticks_to_unix_ns(ticks: int) -> int:
    """A record time as nanoseconds since 1970-01-01 UTC."""
    return (ticks - TICKS_AT_UNIX_EPOCH) * 100


def ticks_to_datetime(ticks: int) -> datetime:
    """A record time as a timezone-aware UTC datetime (to the microsecond)."""
    return _EPOCH + timedelta(microseconds=(ticks - TICKS_AT_UNIX_EPOCH) // 10)


def unix_ns_to_datetime(ns: int) -> datetime:
    """Nanoseconds since 1970-01-01 UTC (the order log's block times) as a UTC datetime."""
    return _EPOCH + timedelta(microseconds=ns // 1000)


def datetime_to_ticks(moment: datetime) -> int:
    """A datetime as a record time. A naive datetime is taken to be UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = moment - _EPOCH
    return TICKS_AT_UNIX_EPOCH + (delta.days * 86_400 + delta.seconds) * 10_000_000 + delta.microseconds * 10
