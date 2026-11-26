"""Baseline forecasters. Each must be beaten before a fancier model is worth anything.

All of them read only columns of `fct_hourly_features`, which are known at issue time
(ADR 0006), so none of them can leak. They return NaN where an input is missing.

Note: the plan's "same hour yesterday" baseline isn't available at 10:00 for most target hours
(ADR 0006), so `naive_48h` (same hour two days earlier) takes its place.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge


class Forecaster(Protocol):
    name: str

    def fit(self, train: pd.DataFrame) -> Forecaster: ...

    def predict(self, df: pd.DataFrame) -> np.ndarray: ...

    def params(self) -> dict[str, object]: ...


class LagNaive:
    """Predict the load from `lag_hours` before the target hour."""

    def __init__(self, lag_hours: int, name: str) -> None:
        self.lag_hours = lag_hours
        self.name = name
        self.column = f"load_lag_{lag_hours}h_mw"

    def fit(self, train: pd.DataFrame) -> LagNaive:
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return df[self.column].to_numpy(dtype=float)

    def params(self) -> dict[str, object]:
        return {"lag_hours": self.lag_hours}


class LinearCalendarWeather:
    """Ridge regression on the calendar and degree-hours.

    Features: one-hot hour, day of week and month; holiday and bridge-day flags; heating and
    cooling degrees, each also crossed with hour of day, because a hot afternoon adds far more
    load than a hot night. That's enough for the U-shaped temperature response and the daily
    cycle, and it's still a linear model.
    """

    name = "linear_calendar_weather"
    INPUTS = (
        "hour_local",
        "dow_local",
        "month_local",
        "is_major_holiday",
        "is_bridge_day",
        "cooling_degree_f",
        "heating_degree_f",
    )

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.model = Ridge(alpha=alpha)
        self.columns_: list[str] | None = None

    def design(self, df: pd.DataFrame) -> pd.DataFrame:
        hour = pd.Categorical(df["hour_local"], categories=range(24))
        X = pd.concat(
            [
                pd.get_dummies(hour, prefix="hour", dtype=float),
                pd.get_dummies(
                    pd.Categorical(df["dow_local"], categories=range(1, 8)),
                    prefix="dow",
                    dtype=float,
                ),
                pd.get_dummies(
                    pd.Categorical(df["month_local"], categories=range(1, 13)),
                    prefix="month",
                    dtype=float,
                ),
            ],
            axis=1,
        )
        X.index = df.index
        X["major_holiday"] = df["is_major_holiday"].astype(float)
        X["bridge_day"] = df["is_bridge_day"].astype(float)
        for deg in ("cooling_degree_f", "heating_degree_f"):
            X[deg] = df[deg].astype(float)
            for h in range(24):
                X[f"{deg}_x_hour_{h}"] = X[deg] * X[f"hour_{h}"]
        return X

    def _usable(self, df: pd.DataFrame) -> pd.Series:
        return df[list(self.INPUTS)].notna().all(axis=1)

    def fit(self, train: pd.DataFrame) -> LinearCalendarWeather:
        rows = train[self._usable(train) & train["load_mw"].notna()]
        if rows.empty:
            raise ValueError("No training rows with complete inputs")
        X = self.design(rows)
        self.columns_ = list(X.columns)
        self.model.fit(X.to_numpy(), rows["load_mw"].to_numpy())
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        out = np.full(len(df), np.nan)
        ok = self._usable(df).to_numpy()
        if ok.any():
            X = self.design(df[ok])[self.columns_]
            out[ok] = self.model.predict(X.to_numpy())
        return out

    def params(self) -> dict[str, object]:
        return {"alpha": self.alpha, "n_features": len(self.columns_ or [])}


def default_baselines() -> list[Forecaster]:
    return [
        LagNaive(168, "seasonal_naive_week"),
        LagNaive(48, "naive_48h"),
        LinearCalendarWeather(),
    ]
