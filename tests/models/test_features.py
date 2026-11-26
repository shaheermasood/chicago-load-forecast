import numpy as np
import pandas as pd
import pytest

from chiload.features.build import BASE_FEATURES, FEATURES, build

from .conftest import make_features


@pytest.fixture(scope="module")
def frame():
    return make_features("2023-01-01", "2023-03-31 23:00")


def test_columns_and_types(frame):
    X = build(frame)
    assert list(X.columns) == FEATURES
    assert all(pd.api.types.is_float_dtype(t) for t in X.dtypes)
    assert X.index.equals(frame.index)


def test_rolling_temperature_uses_only_earlier_hours(frame):
    X = build(frame)
    i = 500
    bumped = frame.copy()
    bumped.loc[bumped.index[i], "temp_f"] += 100
    Xb = build(bumped)
    # The current hour's own rolling value is unchanged; the next hour's moves.
    assert Xb["temp_mean_prev_24h_f"].iloc[i] == X["temp_mean_prev_24h_f"].iloc[i]
    assert Xb["temp_mean_prev_24h_f"].iloc[i + 1] > X["temp_mean_prev_24h_f"].iloc[i + 1]
    # ...and 24 hours later it has dropped out of the 24h window, but not the 72h one.
    assert Xb["temp_mean_prev_24h_f"].iloc[i + 25] == pytest.approx(
        X["temp_mean_prev_24h_f"].iloc[i + 25]
    )
    assert Xb["temp_mean_prev_72h_f"].iloc[i + 25] > X["temp_mean_prev_72h_f"].iloc[i + 25]


def test_row_order_does_not_matter(frame):
    shuffled = frame.sample(frac=1, random_state=0)
    pd.testing.assert_frame_equal(build(shuffled).loc[frame.index], build(frame))


def test_holiday_tier_codes(frame):
    f = frame.iloc[:3].copy()
    f["holiday_tier"] = ["major", None, "bridge"]
    assert build(f)["holiday_tier_code"].tolist() == [3.0, 0.0, 2.0]


def test_interactions(frame):
    X = build(frame)
    np.testing.assert_allclose(X["temp_x_hour_sin"], X["temp_f"] * X["hour_sin"])


def test_missing_mart_column_is_a_clear_error(frame):
    with pytest.raises(KeyError, match=BASE_FEATURES[0]):
        build(frame.drop(columns=BASE_FEATURES[0]))
