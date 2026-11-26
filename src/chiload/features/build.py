"""Turn `fct_hourly_features` rows into a model matrix.

The mart already holds everything known at issue time (ADR 0006). This module only adds
transformations of those columns, so nothing here can leak:

* temperature crossed with time of day: the same heat adds far more load at 4 pm than at 4 am
* rolling forecast temperature over the previous 24 and 72 hours: buildings heat up and
  cool down slowly, so a third hot day draws more than the first
* holiday tier as an integer code

Rolling windows use the forecast temperature already on each earlier row of the mart. Each
of those came from a run published before that row's own issue time, which is earlier than
or equal to ours. They need a continuous, sorted hourly frame, so `build` sorts by target hour
and rolls on a time index; gaps just shorten the window.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Columns taken straight from the mart.
BASE_FEATURES = [
    # load history known at issue time
    "load_lag_48h_mw",
    "load_lag_72h_mw",
    "load_lag_168h_mw",
    "load_lag_336h_mw",
    "load_last_known_mw",
    "load_mean_24h_at_issue_mw",
    "load_mean_168h_at_issue_mw",
    "load_max_24h_at_issue_mw",
    "horizon_hours",
    # calendar
    "hour_local",
    "dow_local",
    "month_local",
    "doy_local",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
    "is_weekend",
    "is_major_holiday",
    "is_bridge_day",
    "is_dst",
    # weather forecast as issued
    "weather_lead_days",
    "temp_f",
    "dew_point_f",
    "apparent_temp_f",
    "rh_pct",
    "wind_mph",
    "cloud_cover_pct",
    "cooling_degree_f",
    "heating_degree_f",
    "temp_spread_f",
]

DERIVED_FEATURES = [
    "holiday_tier_code",
    "temp_x_hour_sin",
    "temp_x_hour_cos",
    "cooling_x_hour_sin",
    "cooling_x_hour_cos",
    "temp_mean_prev_24h_f",
    "temp_mean_prev_72h_f",
    "temp_max_prev_24h_f",
]

FEATURES = BASE_FEATURES + DERIVED_FEATURES

_TIERS = {"major": 3, "bridge": 2, "minor": 1}


def build(df: pd.DataFrame) -> pd.DataFrame:
    """Return the feature matrix (columns = FEATURES), indexed like `df`."""
    missing = [c for c in BASE_FEATURES if c not in df.columns]
    if missing:
        raise KeyError(f"Feature mart is missing columns: {missing}")

    X = df[BASE_FEATURES].copy()
    for c in X.columns:
        if X[c].dtype == bool:
            X[c] = X[c].astype(float)
    X = X.astype(float)

    tier = df["holiday_tier"] if "holiday_tier" in df else pd.Series(None, index=df.index)
    X["holiday_tier_code"] = tier.map(_TIERS).fillna(0).astype(float)
    X["temp_x_hour_sin"] = X["temp_f"] * X["hour_sin"]
    X["temp_x_hour_cos"] = X["temp_f"] * X["hour_cos"]
    X["cooling_x_hour_sin"] = X["cooling_degree_f"] * X["hour_sin"]
    X["cooling_x_hour_cos"] = X["cooling_degree_f"] * X["hour_cos"]

    # Rolling stats over earlier target hours (closed="left": the current hour is excluded).
    temp = pd.Series(X["temp_f"].to_numpy(), index=pd.DatetimeIndex(df["target_period_utc"]))
    order = np.argsort(temp.index.to_numpy(), kind="stable")
    sorted_temp = temp.iloc[order]
    rolled = {
        "temp_mean_prev_24h_f": sorted_temp.rolling("24h", closed="left").mean(),
        "temp_mean_prev_72h_f": sorted_temp.rolling("72h", closed="left").mean(),
        "temp_max_prev_24h_f": sorted_temp.rolling("24h", closed="left").max(),
    }
    inverse = np.empty_like(order)
    inverse[order] = np.arange(len(order))
    for name, series in rolled.items():
        X[name] = series.to_numpy()[inverse]

    return X[FEATURES]
