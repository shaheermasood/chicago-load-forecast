"""Read the feature mart built by dbt (`marts.fct_hourly_features`)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from chiload.config import DATA_DIR

DEFAULT_DB = DATA_DIR / "warehouse.duckdb"
FEATURES_TABLE = "marts.fct_hourly_features"


def load_features(db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    """Return the feature mart sorted by target hour, with `target_date_local` as a date."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"{db_path} not found. Run `make backfill` then `make build`.")
    with duckdb.connect(str(db_path), read_only=True) as con:
        con.execute("set TimeZone = 'UTC'")
        df = con.sql(f"select * from {FEATURES_TABLE} order by target_period_utc").df()
    df["target_date_local"] = pd.to_datetime(df["target_date_local"]).dt.date
    return df


def evaluable(df: pd.DataFrame) -> pd.Series:
    """Rows whose target is a real observation: reported by EIA, not anomalous, not imputed.

    Models may train on imputed hours, but they are scored only against what EIA reported.
    """
    return df["load_mw"].notna() & ~df["is_load_imputed"] & ~df["is_load_anomalous"]
