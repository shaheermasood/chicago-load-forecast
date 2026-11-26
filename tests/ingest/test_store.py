"""The store is what makes ingestion idempotent, so it gets the most careful tests."""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from chiload.ingest.store import (
    FETCHED_AT,
    REVISIONS_DIR,
    existing_days,
    partition_path,
    write_partition,
)

DAY = date(2024, 7, 1)
T1 = datetime(2024, 7, 2, 12, tzinfo=UTC)
T2 = datetime(2024, 7, 3, 12, tzinfo=UTC)
KEYS = ["period_utc"]


def hours(n, values):
    return pd.DataFrame(
        {
            "period_utc": pd.date_range("2024-07-01", periods=n, freq="h", tz="UTC"),
            "load_mw": values,
        }
    )


def read(root):
    return pd.read_parquet(partition_path(root, DAY))


def test_first_write_creates_partition_with_fetch_time(tmp_path):
    r = write_partition(hours(3, [1.0, 2.0, 3.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=T1)
    assert (r.status, r.rows) == ("created", 3)
    stored = read(tmp_path)
    assert stored["load_mw"].tolist() == [1.0, 2.0, 3.0]
    assert (stored[FETCHED_AT] == pd.Timestamp(T1)).all()


def test_identical_rewrite_leaves_file_untouched(tmp_path):
    df = hours(3, [1.0, float("nan"), 3.0])  # NaN must compare equal to NaN
    write_partition(df, tmp_path, DAY, key_cols=KEYS, fetched_at=T1)
    path = partition_path(tmp_path, DAY)
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    r = write_partition(df.sample(frac=1), tmp_path, DAY, key_cols=KEYS, fetched_at=T2)

    assert r.status == "unchanged"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (tmp_path / REVISIONS_DIR).exists()


def test_revised_value_gets_new_fetch_time_and_old_value_is_archived(tmp_path):
    write_partition(hours(3, [1.0, 2.0, 3.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=T1)
    r = write_partition(hours(3, [1.0, 2.5, 3.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=T2)

    assert (r.status, r.changed) == ("updated", 1)
    stored = read(tmp_path)
    assert stored["load_mw"].tolist() == [1.0, 2.5, 3.0]
    assert stored[FETCHED_AT].tolist() == [pd.Timestamp(T1), pd.Timestamp(T2), pd.Timestamp(T1)]

    revs = list((tmp_path / REVISIONS_DIR).rglob("*.parquet"))
    assert len(revs) == 1
    old = pd.read_parquet(revs[0])
    assert old["load_mw"].tolist() == [2.0]
    assert old[FETCHED_AT].tolist() == [pd.Timestamp(T1)]


def test_new_hours_on_a_partial_day_are_added_without_a_revision(tmp_path):
    write_partition(hours(2, [1.0, 2.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=T1)
    r = write_partition(hours(4, [1.0, 2.0, 3.0, 4.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=T2)

    assert (r.status, r.rows, r.changed) == ("updated", 4, 0)
    assert read(tmp_path)[FETCHED_AT].tolist() == [pd.Timestamp(T1)] * 2 + [pd.Timestamp(T2)] * 2
    assert not (tmp_path / REVISIONS_DIR).exists()


def test_rewrite_never_duplicates_rows(tmp_path):
    for t in (T1, T2, T1):
        write_partition(hours(24, range(24)), tmp_path, DAY, key_cols=KEYS, fetched_at=t)
    assert len(read(tmp_path)) == 24


def test_schema_change_rewrites_and_archives_everything(tmp_path):
    write_partition(hours(2, [1.0, 2.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=T1)
    df = hours(2, [1.0, 2.0]).assign(extra=[0.0, 0.0])
    r = write_partition(df, tmp_path, DAY, key_cols=KEYS, fetched_at=T2)
    assert (r.status, r.changed) == ("updated", 2)
    assert "extra" in read(tmp_path)


def test_multi_column_keys(tmp_path):
    df = pd.DataFrame(
        {
            "period_utc": pd.to_datetime(["2024-07-01T00"] * 2, utc=True),
            "point": ["a", "b"],
            "temp_f": [70.0, 71.0],
        }
    )
    keys = ["period_utc", "point"]
    write_partition(df, tmp_path, DAY, key_cols=keys, fetched_at=T1)
    r = write_partition(df, tmp_path, DAY, key_cols=keys, fetched_at=T2)
    assert r.status == "unchanged"


def test_rejects_duplicate_keys_and_naive_times(tmp_path):
    dup = pd.concat([hours(1, [1.0]), hours(1, [2.0])])
    with pytest.raises(ValueError, match="Duplicate"):
        write_partition(dup, tmp_path, DAY, key_cols=KEYS, fetched_at=T1)
    with pytest.raises(ValueError, match="timezone"):
        write_partition(
            hours(1, [1.0]), tmp_path, DAY, key_cols=KEYS, fetched_at=datetime(2024, 1, 1)
        )


def test_existing_days(tmp_path):
    assert existing_days(tmp_path / "nope") == []
    for d in (date(2024, 7, 2), date(2024, 7, 1)):
        write_partition(hours(1, [1.0]), tmp_path, d, key_cols=KEYS, fetched_at=T1)
    (tmp_path / "date=garbage").mkdir()
    (tmp_path / "date=garbage" / "part.parquet").write_bytes(b"")
    assert existing_days(tmp_path) == [date(2024, 7, 1), date(2024, 7, 2)]
