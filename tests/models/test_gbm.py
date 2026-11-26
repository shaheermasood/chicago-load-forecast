import pandas as pd
import pytest

from chiload.features.build import FEATURES
from chiload.models.gbm import LightGBMForecaster
from chiload.models.metrics import mape

FAST = {"n_estimators": 120, "learning_rate": 0.08, "num_leaves": 31}


@pytest.fixture(scope="module")
def split(features):
    day = pd.to_datetime(features["target_date_local"])
    return features[day < "2023-07-01"], features[(day >= "2023-07-01") & (day < "2023-10-01")]


@pytest.fixture(scope="module")
def fitted(split):
    return LightGBMForecaster(FAST).fit(split[0])


def test_point_forecast_beats_seasonal_naive(fitted, split):
    test = split[1]
    y = test["load_mw"].to_numpy()
    assert mape(y, fitted.predict(test)) < 0.6 * mape(y, test["load_lag_168h_mw"].to_numpy())


def test_intervals_are_valid_and_roughly_calibrated(fitted, split):
    test = split[1]
    lo, hi = fitted.predict_interval(test)
    y = test["load_mw"].to_numpy()
    assert (lo <= hi).all()
    assert 0.7 <= ((y >= lo) & (y <= hi)).mean() <= 0.92
    assert fitted.n_calibration_ > 24 * 50


def test_calibration_fixes_a_badly_miscalibrated_quantile_model(split):
    # Five trees can't learn the quantiles, so the raw interval is far too narrow. On the
    # calibration days, conformal adjustment guarantees at least the target coverage.
    train = split[0]
    m = LightGBMForecaster({**FAST, "n_estimators": 5}).fit(train)
    last = max(train["target_date_local"])
    calib = train[
        pd.to_datetime(train["target_date_local"]) > pd.Timestamp(last) - pd.Timedelta(days=60)
    ]
    lo, hi = m.predict_interval(calib)
    y = calib["load_mw"].to_numpy()
    assert ((y >= lo) & (y <= hi)).mean() >= 0.8
    assert m.conformal_adjustment_mw_ > 0  # it had to widen


def test_feature_importance_covers_all_features(fitted):
    imp = fitted.feature_importance()
    assert set(imp.index) == set(FEATURES)
    assert imp.iloc[0] > 0


def test_params_are_loggable(fitted):
    p = fitted.params()
    assert p["n_estimators"] == 120 and p["interval"] == 0.8 and "verbose" not in p


def test_needs_enough_history_for_calibration(features):
    with pytest.raises(ValueError, match="calibration"):
        LightGBMForecaster(FAST, calibration_days=60).fit(features.iloc[: 24 * 30])
