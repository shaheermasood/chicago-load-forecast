import numpy as np
import pandas as pd
import pytest

from chiload.models.metrics import bias_pct, daily_peak_mape, mape, rmse, score


def test_basic_metrics():
    y = np.array([100.0, 200.0])
    yhat = np.array([110.0, 180.0])
    assert mape(y, yhat) == pytest.approx(10.0)
    assert rmse(y, yhat) == pytest.approx(np.sqrt((100 + 400) / 2))
    assert bias_pct(y, yhat) == pytest.approx(-5 / 150 * 100)  # mean error -5 on mean load 150


def test_daily_peak_mape_uses_each_days_max_and_skips_short_days():
    days = pd.Series(["d1"] * 24 + ["d2"] * 24 + ["d3"] * 5)
    y = np.r_[np.full(24, 100.0), np.full(24, 200.0), np.full(5, 50.0)]
    yhat = y.copy()
    yhat[3] = 120.0  # d1 peak forecast 120 vs actual 100 -> 20%
    yhat[30] = 190.0  # d2: a lower value doesn't change the max -> 0%
    yhat[50] = 500.0  # d3 has only 5 hours: skipped
    assert daily_peak_mape(days, y, yhat, min_hours=20) == pytest.approx(10.0)


def test_score_keys():
    out = score(pd.Series(["d"] * 24), np.full(24, 10.0), np.full(24, 11.0))
    assert set(out) == {"mape", "rmse", "bias_pct", "daily_peak_mape", "n_hours"}
