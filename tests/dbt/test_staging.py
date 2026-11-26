"""Run `dbt build` on small synthetic raw data and check the staging models.

The fixture data covers both 2024 DST changes, a missing load value and two weather lead
times. It's written with the real ingestion store, so the Parquet layout matches production.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from chiload.config import PROJECT_ROOT
from chiload.ingest.store import write_partition

pytest.importorskip("dbt.cli.main")
DBT_BIN = Path(sys.executable).parent / "dbt"

DBT_DIR = PROJECT_ROOT / "dbt"
FETCHED = datetime(2024, 12, 1, tzinfo=UTC)
POINTS = ["ohare", "midway"]
WEATHER_COLS = ["temp_f", "dew_point_f", "apparent_temp_f", "rh_pct", "wind_mph", "cloud_cover_pct"]
# UTC days around spring-forward (2024-03-10 08:00 UTC) and fall-back (2024-11-03 07:00 UTC).
DAYS = [date(2024, 3, 9), date(2024, 3, 10), date(2024, 3, 11), date(2024, 11, 3)]


def utc_hours(day: date) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(day, tz="UTC"), periods=24, freq="h")


def weather_frame(day: date, lead_days: int | None) -> pd.DataFrame:
    frames = []
    for i, point in enumerate(POINTS):
        hrs = utc_hours(day)
        df = pd.DataFrame({"period_utc": hrs, "point": point})
        if lead_days is not None:
            df["lead_days"] = lead_days
        base = 40.0 + i + np.arange(24) / 2
        for c in WEATHER_COLS:
            df[c] = base if c.endswith("_f") else 50.0
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def write_raw(raw: Path) -> None:
    for day in DAYS:
        load = 9000.0 + np.arange(24) * 100
        if day == date(2024, 3, 10):
            load[3] = np.nan  # EIA sometimes has no value; staging must keep and flag it
        eia = pd.DataFrame(
            {"period_utc": utc_hours(day), "parent": "PJM", "subba": "CE", "load_mw": load}
        )
        write_partition(eia, raw / "eia", day, key_cols=["period_utc"], fetched_at=FETCHED)

        fc = pd.concat([weather_frame(day, 1), weather_frame(day, 2)], ignore_index=True)
        write_partition(
            fc,
            raw / "weather" / "source=previous_runs",
            day,
            key_cols=["period_utc", "point", "lead_days"],
            fetched_at=FETCHED,
        )
        write_partition(
            weather_frame(day, None),
            raw / "weather" / "source=era5",
            day,
            key_cols=["period_utc", "point"],
            fetched_at=FETCHED,
        )


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory) -> Path:
    """Build the staging layer in a subprocess, as `make build` would, and return the DB path."""
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
        "--select",
        "staging",
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    return db


def query(db: Path, sql: str) -> pd.DataFrame:
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("set TimeZone = 'UTC'")
        return con.sql(sql).df()


def test_demand_has_one_row_per_utc_hour(warehouse):
    df = query(warehouse, "select * from staging.stg_eia__hourly_demand order by period_utc")
    assert len(df) == 24 * len(DAYS)
    assert df["period_utc"].is_unique
    assert df["subregion_code"].eq("CE").all()


def test_missing_load_is_kept_and_flagged(warehouse):
    df = query(warehouse, "select * from staging.stg_eia__hourly_demand where is_load_missing")
    assert len(df) == 1
    assert df["period_utc"].iloc[0] == pd.Timestamp("2024-03-10T03:00", tz="UTC")


def test_local_time_handles_spring_forward(warehouse):
    df = query(
        warehouse,
        """
        select period_utc, period_local from staging.stg_eia__hourly_demand
        where period_utc between '2024-03-10 07:00+00' and '2024-03-10 08:00+00'
        order by period_utc
        """,
    )
    # 07:00 UTC is 01:00 CST; one hour later clocks jump to 03:00 CDT. 02:00 never happens.
    assert df["period_local"].dt.strftime("%H:%M").tolist() == ["01:00", "03:00"]


def test_local_time_repeats_at_fall_back(warehouse):
    df = query(
        warehouse,
        """
        select period_local, count(*) as n from staging.stg_eia__hourly_demand
        where cast(period_local as date) = '2024-11-03' and hour(period_local) = 1
        group by 1
        """,
    )
    assert df["n"].tolist() == [2]  # 01:00 CDT and 01:00 CST: two UTC hours, same local label


def test_forecast_grain_and_leads(warehouse):
    df = query(warehouse, "select * from staging.stg_open_meteo__weather_forecast")
    assert len(df) == 24 * len(DAYS) * len(POINTS) * 2
    assert sorted(df["lead_days"].unique()) == [1, 2]
    assert not df.duplicated(["period_utc", "point", "lead_days"]).any()
    assert set(WEATHER_COLS) <= set(df.columns)


def test_reanalysis_grain(warehouse):
    df = query(warehouse, "select * from staging.stg_open_meteo__weather_reanalysis")
    assert len(df) == 24 * len(DAYS) * len(POINTS)
    assert "lead_days" not in df.columns


def test_gap_check_flags_the_jump_between_fixture_months(warehouse):
    # Fixture has 2024-03-11 then 2024-11-03: exactly one gap > 2 hours in demand.
    df = query(
        warehouse,
        """
        select count(*) as n from (
            select period_utc - lag(period_utc) over (order by period_utc) as gap
            from staging.stg_eia__hourly_demand
        ) where gap > interval 2 hour
        """,
    )
    assert df["n"].item() == 1
