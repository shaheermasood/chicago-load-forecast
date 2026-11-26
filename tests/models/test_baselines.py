import numpy as np

from chiload.models.baselines import LagNaive, LinearCalendarWeather, default_baselines
from chiload.models.metrics import mape


def test_lag_naive_reads_the_lag_column(features):
    m = LagNaive(168, "week").fit(features)
    np.testing.assert_array_equal(m.predict(features), features["load_lag_168h_mw"].to_numpy())


def test_linear_model_learns_the_structure(features):
    train = features[features["target_date_local"].astype(str) < "2024-01-01"]
    test = features[features["target_date_local"].astype(str) >= "2024-01-01"]
    pred = LinearCalendarWeather().fit(train).predict(test)
    week = test["load_lag_168h_mw"].to_numpy()
    y = test["load_mw"].to_numpy()
    assert mape(y, pred) < 0.7 * mape(y, week)  # temperature helps a lot on this data


def test_linear_model_returns_nan_without_weather(features):
    model = LinearCalendarWeather().fit(features.iloc[:5000])
    test = features.iloc[5000:5048].copy()
    test.loc[test.index[:10], "cooling_degree_f"] = np.nan
    pred = model.predict(test)
    assert np.isnan(pred[:10]).all() and np.isfinite(pred[10:]).all()


def test_default_baselines_have_unique_names():
    names = [m.name for m in default_baselines()]
    assert len(names) == len(set(names)) == 3
