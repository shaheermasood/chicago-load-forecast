"""Date-range helpers shared by the loaders. All dates are UTC calendar days."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta


def days(start: date, end: date) -> Iterator[date]:
    """Every day from start to end, inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split [start, end] (inclusive) into pieces that never cross a month boundary."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    cur = start
    while cur <= end:
        next_month = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(end, next_month - timedelta(days=1))
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)
