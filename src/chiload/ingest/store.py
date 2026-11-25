"""Idempotent, date-partitioned Parquet storage for raw data.

Layout under a source's root directory:

    date=YYYY-MM-DD/part.parquet                      current values for that UTC day
    _revisions/date=YYYY-MM-DD/superseded_at=<ts>.parquet   rows that a later fetch changed

Rules that make re-runs safe:

* Re-writing a day replaces that day's partition. It never appends, so rows can't duplicate.
* If a fetch returns exactly what is already stored, nothing is written. A second backfill
  leaves every file byte-for-byte unchanged.
* Each row has `fetched_at_utc`: when this value was first seen. Unchanged rows keep their
  original timestamp, and changed rows get the new one. The old version of a changed or removed
  row goes to `_revisions/`, so you can still reconstruct "what was known when" (EIA-930
  revises data after first publication).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import pandas as pd

log = logging.getLogger(__name__)

FETCHED_AT = "fetched_at_utc"
PART_FILE = "part.parquet"
REVISIONS_DIR = "_revisions"

Status = Literal["created", "updated", "unchanged", "missing"]


@dataclass(frozen=True)
class WriteResult:
    day: date
    status: Status
    rows: int = 0
    changed: int = 0  # rows whose value changed or disappeared vs. what was stored


def partition_path(root: Path, day: date) -> Path:
    return root / f"date={day.isoformat()}" / PART_FILE


def existing_days(root: Path) -> list[date]:
    """Sorted list of days that have a partition under `root`."""
    if not root.exists():
        return []
    days = []
    for p in root.glob(f"date=*/{PART_FILE}"):
        try:
            days.append(date.fromisoformat(p.parent.name.removeprefix("date=")))
        except ValueError:
            log.warning("Ignoring unexpected partition directory %s", p.parent)
    return sorted(days)


def write_partition(
    df: pd.DataFrame,
    root: Path,
    day: date,
    *,
    key_cols: list[str],
    fetched_at: datetime,
) -> WriteResult:
    """Write one day's rows, keeping the partition free of duplicates and tracking revisions.

    `df` holds the key columns plus value columns, and no `fetched_at_utc` column.
    """
    if FETCHED_AT in df.columns:
        raise ValueError(f"{FETCHED_AT} is set by the store; drop it from the input")
    if df.duplicated(key_cols).any():
        raise ValueError(f"Duplicate keys {key_cols} in rows for {day}")

    ts = pd.Timestamp(fetched_at).tz_convert("UTC") if fetched_at.tzinfo else None
    if ts is None:
        raise ValueError("fetched_at must be timezone-aware")

    new = _normalize(df).sort_values(key_cols).reset_index(drop=True)
    path = partition_path(root, day)

    if not path.exists():
        new[FETCHED_AT] = ts
        _atomic_write(new, path)
        return WriteResult(day, "created", rows=len(new))

    old = _normalize(pd.read_parquet(path))
    value_cols = [c for c in new.columns if c not in key_cols]
    old_value_cols = [c for c in old.columns if c not in key_cols and c != FETCHED_AT]

    if set(value_cols) != set(old_value_cols):
        # Schema changed (e.g. a weather variable was added): treat every stored row as revised.
        log.warning("Columns changed for %s; rewriting partition %s", day, path)
        same_keys = old.iloc[0:0][key_cols + [FETCHED_AT]]
    else:
        merged = old.merge(new, on=key_cols, how="inner", suffixes=("_old", ""))
        unchanged = pd.Series(True, index=merged.index)
        for c in value_cols:
            a, b = merged[f"{c}_old"], merged[c]
            unchanged &= (a == b).fillna(False).astype(bool) | (a.isna() & b.isna())
        same_keys = merged.loc[unchanged, key_cols + [FETCHED_AT]]

    superseded = _anti_join(old, same_keys, key_cols)
    added = _anti_join(new, same_keys, key_cols)

    if superseded.empty and added.empty:
        return WriteResult(day, "unchanged", rows=len(old))

    # Unchanged rows keep the time they were first seen; new or changed rows get this fetch's.
    out = new.merge(same_keys, on=key_cols, how="left")
    out[FETCHED_AT] = out[FETCHED_AT].fillna(ts).astype("datetime64[us, UTC]")

    if not superseded.empty:
        rev_path = (
            root
            / REVISIONS_DIR
            / f"date={day.isoformat()}"
            / f"superseded_at={ts.strftime('%Y%m%dT%H%M%SZ')}.parquet"
        )
        _atomic_write(superseded.reset_index(drop=True), rev_path)

    _atomic_write(out, path)
    return WriteResult(day, "updated", rows=len(out), changed=len(superseded))


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Make dtypes stable across a Parquet round trip so comparisons are exact."""
    out = df.copy()
    for c in out.columns:
        if isinstance(out[c].dtype, pd.DatetimeTZDtype):
            out[c] = out[c].dt.tz_convert("UTC").astype("datetime64[us, UTC]")
    return out


def _anti_join(left: pd.DataFrame, right: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if right.empty:
        return left
    marker = left.merge(right[keys], on=keys, how="left", indicator=True)["_merge"]
    return left.loc[(marker == "left_only").to_numpy()]


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
