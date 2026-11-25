from datetime import date

import pytest

from chiload.ingest.dates import days, month_chunks


def test_month_chunks_split_at_month_boundaries():
    assert list(month_chunks(date(2024, 1, 15), date(2024, 3, 2))) == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 2)),
    ]


def test_single_day_and_bad_range():
    assert list(month_chunks(date(2024, 12, 31), date(2024, 12, 31))) == [
        (date(2024, 12, 31), date(2024, 12, 31))
    ]
    with pytest.raises(ValueError):
        list(month_chunks(date(2024, 2, 1), date(2024, 1, 1)))


def test_days_inclusive():
    assert list(days(date(2024, 2, 28), date(2024, 3, 1))) == [
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
    ]
