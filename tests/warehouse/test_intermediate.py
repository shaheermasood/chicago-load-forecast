import pandas as pd
import pytest

from .conftest import NAN_HOUR, POINTS, SPIKE_HOUR, ZERO_HOUR, point_temp, true_load

H = pd.Timestamp


def load_at(query, ts: str) -> pd.Series:
    df = query(f"select * from intermediate.int_load_hourly where period_utc = '{ts}+00'")
    assert len(df) == 1, ts
    return df.iloc[0]


def test_spine_has_every_hour(query):
    df = query(
        "select count(*) as n, min(period_utc) as lo, max(period_utc) as hi "
        "from intermediate.int_load_hourly"
    ).iloc[0]
    assert df["n"] == (df["hi"] - df["lo"]) / pd.Timedelta(hours=1) + 1


@pytest.mark.parametrize("ts", ["2024-03-15 10:00", "2024-03-15 11:00"])
def test_short_gap_is_interpolated_and_flagged(query, ts):
    row = load_at(query, ts)
    assert row["is_reported_missing"] and row["is_imputed"]
    assert row["load_mw"] == pytest.approx(true_load(pd.DatetimeIndex([H(ts)]))[0], rel=0.02)


def test_missing_value_is_interpolated(query):
    row = load_at(query, str(NAN_HOUR.tz_localize(None)))
    assert row["is_reported_missing"] and row["is_imputed"]


@pytest.mark.parametrize("ts", [ZERO_HOUR, SPIKE_HOUR])
def test_anomalies_are_flagged_and_replaced(query, ts):
    row = load_at(query, str(ts.tz_localize(None)))
    assert row["is_anomalous"] and row["is_imputed"]
    assert row["reported_load_mw"] != row["load_mw"]  # original value kept for inspection
    assert row["load_mw"] == pytest.approx(true_load(pd.DatetimeIndex([ts]))[0], rel=0.02)


def test_neighbours_of_a_spike_are_not_flagged(query):
    df = query(
        """
        select count(*) as n from intermediate.int_load_hourly
        where is_anomalous
          and period_utc between '2024-03-01 00:00+00' and '2024-03-20 23:00+00'
        """
    )
    assert df["n"].item() == 2  # only the zero and the spike


def test_long_gap_is_left_null(query):
    row = load_at(query, "2024-03-18 02:00")
    assert pd.isna(row["load_mw"]) and not row["is_imputed"]


def test_zone_temperature_is_population_weighted(query):
    ts = H("2024-03-05T18:00", tz="UTC")
    df = query(
        "select temp_f, n_points from intermediate.int_weather_zone_hourly "
        f"where weather_source = 'forecast' and lead_days = 1 and period_utc = '{ts}'"
    ).iloc[0]
    temps = [point_temp(pd.DatetimeIndex([ts]), i)[0] for i in range(len(POINTS))]
    expected = sum(t * p["weight"] for t, p in zip(temps, POINTS, strict=True))
    assert df["temp_f"] == pytest.approx(expected)
    assert df["n_points"] == len(POINTS)


def test_issue_time_is_10am_central_the_day_before(query):
    df = query(
        "select * from intermediate.int_forecast_issues "
        "where target_date_local in ('2024-03-11', '2024-11-04') order by 1"
    )
    # 10:00 CDT on 2024-03-10 is 15:00 UTC; 10:00 CST on 2024-11-03 is 16:00 UTC.
    assert df["issue_time_utc"].tolist() == [
        H("2024-03-10T15:00", tz="UTC"),
        H("2024-11-03T16:00", tz="UTC"),
    ]
    assert (df["load_known_through_utc"] == df["issue_time_utc"] - pd.Timedelta(hours=7)).all()
