"""Build the whole dbt project once on small synthetic raw data.

The data is written with the real ingestion store, so the Parquet layout matches production.
It covers both 2024 DST changes, with continuous weeks so the 168-hour lags have history.
It also includes known data problems, each with a test in test_intermediate.py:

    2024-03-10 03:00 UTC   EIA row with no value          -> kept, flagged, interpolated
    2024-03-15 10-11 UTC   two hours missing entirely     -> interpolated, flagged
    2024-03-15 14:00 UTC   reported 0 MW                  -> anomalous, interpolated
    2024-03-16 12:00 UTC   one-hour spike (x1.6)          -> anomalous, interpolated
    2024-03-18 00-05 UTC   six hours missing              -> too long: load_mw stays NULL
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from chiload.config import PROJECT_ROOT, load_yaml
from chiload.ingest.store import write_partition

pytest.importorskip("dbt.cli.main")
DBT_BIN = Path(sys.executable).parent / "dbt"
DBT_DIR = PROJECT_ROOT / "dbt"
FETCHED = datetime(2024, 12, 1, tzinfo=UTC)
POINTS = load_yaml("weather_points")["points"]
WEATHER_COLS = ["temp_f", "dew_point_f", "apparent_temp_f", "rh_pct", "wind_mph", "cloud_cover_pct"]


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


DAYS = _days(date(2024, 3, 1), date(2024, 3, 20)) + _days(date(2024, 10, 25), date(2024, 11, 8))

H = pd.Timestamp
NAN_HOUR = H("2024-03-10T03:00", tz="UTC")
DROPPED = {H("2024-03-15T10:00", tz="UTC"), H("2024-03-15T11:00", tz="UTC")} | {
    H(f"2024-03-18T0{h}:00", tz="UTC") for h in range(6)
}
ZERO_HOUR = H("2024-03-15T14:00", tz="UTC")
SPIKE_HOUR = H("2024-03-16T12:00", tz="UTC")


def true_load(ts: pd.DatetimeIndex) -> np.ndarray:
    """A smooth daily cycle: hour-to-hour changes stay well under the spike threshold."""
    hours = ts.hour + ts.minute / 60
    return 11_000 + 1_500 * np.sin(2 * np.pi * (hours - 12) / 24)


def point_temp(ts: pd.DatetimeIndex, i: int) -> np.ndarray:
    return 40.0 + i + 10 * np.sin(2 * np.pi * (ts.hour - 15) / 24)


def utc_hours(day: date) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(day, tz="UTC"), periods=24, freq="h")


def weather_frame(day: date, lead_days: int | None, offset: float) -> pd.DataFrame:
    frames = []
    for i, p in enumerate(POINTS):
        hrs = utc_hours(day)
        df = pd.DataFrame({"period_utc": hrs, "point": p["name"]})
        if lead_days is not None:
            df["lead_days"] = lead_days
        temp = point_temp(hrs, i) + offset
        for c in WEATHER_COLS:
            df[c] = temp if c.endswith("_f") else 50.0
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def write_raw(raw: Path) -> None:
    for day in DAYS:
        hrs = utc_hours(day)
        eia = pd.DataFrame(
            {"period_utc": hrs, "parent": "PJM", "subba": "CE", "load_mw": true_load(hrs)}
        )
        eia.loc[eia.period_utc == NAN_HOUR, "load_mw"] = np.nan
        eia.loc[eia.period_utc == ZERO_HOUR, "load_mw"] = 0.0
        eia.loc[eia.period_utc == SPIKE_HOUR, "load_mw"] *= 1.6
        eia = eia[~eia.period_utc.isin(DROPPED)]
        write_partition(eia, raw / "eia", day, key_cols=["period_utc"], fetched_at=FETCHED)

        # Lead 2 runs 1 degree warmer than lead 1 and ERA5 half a degree cooler, so tests can
        # tell which source a value came from.
        fc = pd.concat([weather_frame(day, 1, 0.0), weather_frame(day, 2, 1.0)], ignore_index=True)
        write_partition(
            fc,
            raw / "weather" / "source=previous_runs",
            day,
            key_cols=["period_utc", "point", "lead_days"],
            fetched_at=FETCHED,
        )
        write_partition(
            weather_frame(day, None, -0.5),
            raw / "weather" / "source=era5",
            day,
            key_cols=["period_utc", "point"],
            fetched_at=FETCHED,
        )


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory) -> Path:
    """Run `dbt build` in a subprocess, as `make build` would, and return the DuckDB path."""
    tmp = tmp_path_factory.mktemp("dbt")
    write_raw(tmp / "raw")
    db = tmp / "warehouse.duckdb"
    env = {
        **os.environ,
        "CHILOAD_RAW_DIR": str(tmp / "raw"),
        "CHILOAD_DUCKDB_PATH": str(db),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    cmd = [
        str(DBT_BIN),
        "build",
        "--project-dir",
        str(DBT_DIR),
        "--profiles-dir",
        str(DBT_DIR),
        "--target-path",
        str(tmp / "target"),
        "--log-path",
        str(tmp / "logs"),
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    (tmp / "dbt_stdout.txt").write_text(proc.stdout)
    return db


@pytest.fixture(scope="session")
def query(warehouse):
    def run(sql: str) -> pd.DataFrame:
        with duckdb.connect(str(warehouse), read_only=True) as con:
            con.execute("set TimeZone = 'UTC'")
            return con.sql(sql).df()

    return run
