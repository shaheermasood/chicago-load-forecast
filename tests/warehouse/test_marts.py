import pandas as pd
import pytest

H = pd.Timestamp


@pytest.fixture(scope="module")
def features(query):
    df = query("select * from marts.fct_hourly_features order by target_period_utc")
    df["target_date_local"] = pd.to_datetime(df["target_date_local"]).dt.date
    return df


def test_one_row_per_hour_and_dst_day_lengths(features):
    assert features["target_period_utc"].is_unique
    days = features.drop_duplicates("target_date_local").set_index("target_date_local")
    assert days.loc[pd.Timestamp("2024-03-10").date(), "hours_in_local_day"] == 23
    assert days.loc[pd.Timestamp("2024-11-03").date(), "hours_in_local_day"] == 25
    assert days.loc[pd.Timestamp("2024-03-12").date(), "hours_in_local_day"] == 24


def test_day_ahead_horizons(features):
    day = features[features["target_date_local"] == pd.Timestamp("2024-03-14").date()]
    # Issued 10:00 CDT on the 13th: local midnight is 14 hours later, 23:00 is 37 hours later.
    assert day["horizon_hours"].tolist() == list(range(14, 38))


def test_load_lags_respect_the_cutoff(features):
    day = features[features["target_date_local"] == pd.Timestamp("2024-03-14").date()]
    # Every 168h lag is far in the past, so it's always present.
    assert day["load_lag_168h_mw"].notna().all()
    # load_known_through is issue - 7h = 08:00 UTC on the 13th. A 48h lag is usable only when
    # target - 48h <= that, which holds for every hour of the day.
    assert day["load_lag_48h_mw"].notna().all()


def test_load_known_at_issue(query):
    # Target 2024-03-15 08:00 UTC belongs to local day 2024-03-15 (CDT), issued
    # 2024-03-14 10:00 CDT = 15:00 UTC. With a 6h publication lag, the newest usable hour
    # starts at 15:00 - 6h - 1h = 08:00 UTC, and that is the "last known" load.
    df = query(
        """
        select f.load_last_known_mw, f.load_known_through_utc, f.issue_time_utc, l.load_mw
        from marts.fct_hourly_features f
        join intermediate.int_load_hourly l on l.period_utc = f.load_known_through_utc
        where f.target_period_utc = '2024-03-15 08:00+00'
        """
    ).iloc[0]
    assert df["issue_time_utc"] == H("2024-03-14T15:00", tz="UTC")
    assert df["load_known_through_utc"] == H("2024-03-14T08:00", tz="UTC")
    assert df["load_last_known_mw"] == pytest.approx(df["load_mw"])


def test_lags_are_null_across_long_gaps(features):
    # 2024-03-18 00-05 UTC is a 6-hour gap with no load. A target 168h later has no lag value.
    row = features.set_index("target_period_utc").loc[H("2024-03-25T02:00", tz="UTC")]
    assert pd.isna(row["load_lag_168h_mw"])


def test_weather_lead_switches_from_1_to_2_through_the_day(features):
    day = features[features["target_date_local"] == pd.Timestamp("2024-03-14").date()]
    # Issue 15:00 UTC, run latency 6h: a lead-1 value (run ~24h before target) is published
    # only if target - 24h <= 09:00 UTC on the 13th, i.e. target <= 09:00 UTC on the 14th
    # (04:00 CDT). Later hours must use lead 2.
    leads = day.set_index(day["target_period_local"].dt.hour)["weather_lead_days"]
    assert set(leads.loc[0:4]) == {1}
    assert set(leads.loc[5:23]) == {2}
    assert day["temp_f"].notna().all()


def test_weather_columns_come_from_the_chosen_lead(query):
    df = query(
        """
        select f.temp_f, w.temp_f as lead2_temp
        from marts.fct_hourly_features f
        join intermediate.int_weather_zone_hourly w
          on w.period_utc = f.target_period_utc and w.weather_source = 'forecast'
         and w.lead_days = 2
        where f.weather_lead_days = 2
        """
    )
    assert len(df) > 0
    assert (df["temp_f"] == df["lead2_temp"]).all()


def test_degree_hours(features):
    row = features.dropna(subset=["temp_f"]).iloc[0]
    assert row["cooling_degree_f"] == max(row["temp_f"] - 65, 0)
    assert row["heating_degree_f"] == max(65 - row["temp_f"], 0)


def test_calendar_and_holidays(features):
    by_hour = features.set_index("target_period_local")
    # 2024-03-04 is Casimir Pulaski Day (minor). 2024-11-05 is Election Day (minor).
    assert by_hour.loc["2024-03-04 12:00", "holiday_tier"] == "minor"
    assert not by_hour.loc["2024-03-04 12:00", "is_major_holiday"]
    assert by_hour.loc["2024-03-09 12:00", "is_weekend"]  # Saturday
    assert by_hour.loc["2024-03-12 12:00", "is_dst"]
    assert not by_hour.loc["2024-11-05 12:00", "is_dst"]


def test_oracle_weather_is_separate(query, features):
    assert not any(c.startswith("oracle_") for c in features.columns)
    df = query(
        """
        select f.temp_f - o.oracle_temp_f as diff
        from marts.fct_hourly_features f
        join marts.fct_hourly_weather_oracle o using (target_period_utc)
        where f.weather_lead_days = 1
        """
    )
    assert df["diff"].round(6).eq(0.5).all()  # fixture: ERA5 = lead 1 - 0.5


def test_mart_has_every_column_the_models_read(features):
    from chiload.models.baselines import LinearCalendarWeather

    needed = set(LinearCalendarWeather.INPUTS) | {
        "target_period_utc",
        "target_date_local",
        "load_mw",
        "is_load_imputed",
        "is_load_anomalous",
        "load_known_through_utc",
        "issue_time_utc",
        "load_lag_48h_mw",
        "load_lag_168h_mw",
    }
    assert needed <= set(features.columns)
