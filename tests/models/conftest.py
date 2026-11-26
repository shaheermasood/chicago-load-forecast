"""Synthetic `fct_hourly_features` rows with a known structure, for fast model tests.

Load = daily cycle + weekend dip + temperature response (U-shape) + noise, from 2022 to
2024-12. Local time is approximated as UTC-6 (no DST), which is fine for testing the
backtest machinery. The real DST handling is tested against dbt in tests/warehouse.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_features(start="2022-01-01", end="2024-12-31 23:00", seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    utc = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="h")
    local = (utc - pd.Timedelta(hours=6)).tz_localize(None)
    hour = local.hour.to_numpy()
    doy = local.dayofyear.to_numpy()
    dow = local.isocalendar().day.to_numpy()

    temp = 50 - 25 * np.cos(2 * np.pi * (doy - 15) / 365) + 8 * np.sin(2 * np.pi * (hour - 9) / 24)
    temp = temp + rng.normal(0, 3, len(utc))
    cdd, hdd = np.maximum(temp - 65, 0), np.maximum(65 - temp, 0)
    load = (
        11_000
        + 1_500 * np.sin(2 * np.pi * (hour - 10) / 24)
        - 900 * (dow >= 6)
        + 220 * cdd * (0.6 + 0.4 * np.sin(2 * np.pi * (hour - 9) / 24) ** 2)
        + 60 * hdd
        + rng.normal(0, 150, len(utc))
    )

    df = pd.DataFrame(
        {
            "target_period_utc": utc,
            "target_period_local": local,
            "target_date_local": local.date,
            "load_mw": load,
            "is_load_imputed": False,
            "is_load_anomalous": False,
            "hour_local": hour,
            "dow_local": dow,
            "month_local": local.month.to_numpy(),
            "is_major_holiday": False,
            "is_bridge_day": False,
            "temp_f": temp,
            "cooling_degree_f": cdd,
            "heating_degree_f": hdd,
        }
    )
    for k in (48, 72, 168, 336):
        df[f"load_lag_{k}h_mw"] = df["load_mw"].shift(k)
    # Issued 10:00 local the day before; usable load through issue - 7h (ADR 0006).
    issue = pd.to_datetime(df["target_date_local"]) - pd.Timedelta(days=1) + pd.Timedelta(hours=10)
    df["issue_time_utc"] = (issue + pd.Timedelta(hours=6)).dt.tz_localize("UTC")
    df["load_known_through_utc"] = df["issue_time_utc"] - pd.Timedelta(hours=7)
    df["horizon_hours"] = (df["target_period_utc"] - df["issue_time_utc"]) / pd.Timedelta(hours=1)

    # Load summaries as of each day's cutoff, the same as int_forecast_issues.
    by_time = pd.Series(load, index=utc)
    stats = pd.DataFrame(
        {
            "last": by_time,
            "mean24": by_time.rolling(24).mean(),
            "mean168": by_time.rolling(168).mean(),
            "max24": by_time.rolling(24).max(),
        }
    ).reindex(df["load_known_through_utc"])
    df["load_last_known_mw"] = stats["last"].to_numpy()
    df["load_mean_24h_at_issue_mw"] = stats["mean24"].to_numpy()
    df["load_mean_168h_at_issue_mw"] = stats["mean168"].to_numpy()
    df["load_max_24h_at_issue_mw"] = stats["max24"].to_numpy()

    df["doy_local"] = doy
    df["hour_sin"], df["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    df["dow_sin"], df["dow_cos"] = (
        np.sin(2 * np.pi * (dow - 1) / 7),
        np.cos(2 * np.pi * (dow - 1) / 7),
    )
    df["doy_sin"] = np.sin(2 * np.pi * (doy - 1) / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * (doy - 1) / 365.25)
    df["is_weekend"] = dow >= 6
    df["is_dst"] = False
    df["holiday_tier"] = None
    df["weather_lead_days"] = np.where(df["horizon_hours"] <= 18, 1, 2)
    df["dew_point_f"] = temp - 10
    df["apparent_temp_f"] = temp
    df["rh_pct"] = 60.0
    df["wind_mph"] = 8.0
    df["cloud_cover_pct"] = 40.0
    df["temp_spread_f"] = 3.0
    return df


@pytest.fixture(scope="session")
def features() -> pd.DataFrame:
    return make_features()
