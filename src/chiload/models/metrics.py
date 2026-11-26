"""Forecast error metrics. Each takes actual and predicted arrays with no NaNs."""

from __future__ import annotations

import numpy as np
import pandas as pd


def mape(y: np.ndarray, yhat: np.ndarray) -> float:
    """Mean absolute percentage error, in percent."""
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.mean(np.abs(yhat - y) / y) * 100)


def rmse(y: np.ndarray, yhat: np.ndarray) -> float:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.sqrt(np.mean((yhat - y) ** 2)))


def bias_pct(y: np.ndarray, yhat: np.ndarray) -> float:
    """Mean signed error as a percent of mean load. Positive means over-forecasting."""
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.mean(yhat - y) / np.mean(y) * 100)


def daily_peak_mape(
    days: pd.Series, y: np.ndarray, yhat: np.ndarray, *, min_hours: int = 20
) -> float:
    """MAPE of each local day's peak: |max forecast - max actual| / max actual, averaged.

    Peak load drives grid planning, so a model that gets the daily shape right but the peak
    wrong is a real problem. Days with fewer than `min_hours` scored hours are skipped, since
    their observed max might not be the true peak.
    """
    frame = pd.DataFrame({"day": np.asarray(days), "y": y, "yhat": yhat})
    g = frame.groupby("day").agg(n=("y", "size"), y=("y", "max"), yhat=("yhat", "max"))
    g = g[g["n"] >= min_hours]
    if g.empty:
        return float("nan")
    return float(np.mean(np.abs(g["yhat"] - g["y"]) / g["y"]) * 100)


def score(
    days: pd.Series, y: np.ndarray, yhat: np.ndarray, *, min_hours_per_day: int = 20
) -> dict[str, float]:
    return {
        "mape": mape(y, yhat),
        "rmse": rmse(y, yhat),
        "bias_pct": bias_pct(y, yhat),
        "daily_peak_mape": daily_peak_mape(days, y, yhat, min_hours=min_hours_per_day),
        "n_hours": float(len(y)),
    }
