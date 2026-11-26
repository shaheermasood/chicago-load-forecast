"""LightGBM point forecasts with calibrated 80% prediction intervals.

Point forecast: LightGBM with squared-error loss on the full feature matrix
(`chiload.features.build`).

Intervals: conformalized quantile regression (CQR; Romano, Patterson & Candès, 2019).
1. Hold back the last `calibration_days` of the training window.
2. Fit 10% and 90% quantile models on the rest.
3. On the held-back days, measure how far actual loads fall outside [q10, q90].
4. Widen (or narrow) every interval by the conformal quantile of those misses.

Raw quantile models tend to give intervals that are too narrow out of sample. The calibration
step fixes the width using days the quantile models never saw. Because the calibration days
are the most recent ones, the interval reflects current conditions. Time series aren't fully
exchangeable, so coverage is checked in the backtest, not assumed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from chiload.features.build import FEATURES, build
from chiload.models.data import evaluable

DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 800,
    "learning_rate": 0.03,
    "num_leaves": 63,
    "min_child_samples": 40,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": 42,
    "verbose": -1,
}


class LightGBMForecaster:
    name = "lightgbm"

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        *,
        interval: float = 0.8,
        calibration_days: int = 60,
    ) -> None:
        self.lgb_params = {**DEFAULT_PARAMS, **(params or {})}
        self.interval = interval
        self.calibration_days = calibration_days
        self.alpha_lo = (1 - interval) / 2
        self.alpha_hi = 1 - self.alpha_lo

    def fit(self, train: pd.DataFrame) -> LightGBMForecaster:
        train = train[train["load_mw"].notna()]
        X, y = build(train), train["load_mw"].to_numpy()
        self.point_ = lgb.LGBMRegressor(**self.lgb_params).fit(X, y)

        last_day = max(train["target_date_local"])
        calib = (
            train["target_date_local"] > last_day - timedelta(days=self.calibration_days)
        ).to_numpy()
        if calib.all() or not calib.any():
            raise ValueError("Training window too short to hold back calibration days")

        q = {**self.lgb_params, "objective": "quantile"}
        self.lo_ = lgb.LGBMRegressor(**q, alpha=self.alpha_lo).fit(X[~calib], y[~calib])
        self.hi_ = lgb.LGBMRegressor(**q, alpha=self.alpha_hi).fit(X[~calib], y[~calib])

        # Score only real observations, as in the backtest.
        scored = calib & evaluable(train).to_numpy()
        Xc, yc = X[scored], y[scored]
        misses = np.maximum(self.lo_.predict(Xc) - yc, yc - self.hi_.predict(Xc))
        n = len(misses)
        level = min(1.0, np.ceil((n + 1) * self.interval) / n)
        self.conformal_adjustment_mw_ = float(np.quantile(misses, level, method="higher"))
        self.n_calibration_ = n
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.point_.predict(build(df))

    def predict_interval(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = build(df)
        lo = self.lo_.predict(X) - self.conformal_adjustment_mw_
        hi = self.hi_.predict(X) + self.conformal_adjustment_mw_
        # Quantile models are fit separately and can cross; keep a valid interval.
        return np.minimum(lo, hi), np.maximum(lo, hi)

    def feature_importance(self) -> pd.Series:
        gain = self.point_.booster_.feature_importance(importance_type="gain")
        return pd.Series(gain, index=FEATURES).sort_values(ascending=False)

    def params(self) -> dict[str, object]:
        return {
            **{k: v for k, v in self.lgb_params.items() if k != "verbose"},
            "interval": self.interval,
            "calibration_days": self.calibration_days,
        }
