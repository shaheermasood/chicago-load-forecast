import pandas as pd

from .conftest import DAYS, DROPPED, NAN_HOUR, POINTS


def test_demand_has_one_row_per_reported_hour(query):
    df = query("select * from staging.stg_eia__hourly_demand order by period_utc")
    assert len(df) == 24 * len(DAYS) - len(DROPPED)
    assert df["period_utc"].is_unique
    assert df["subregion_code"].eq("CE").all()


def test_missing_value_is_kept_and_flagged(query):
    df = query("select * from staging.stg_eia__hourly_demand where is_load_missing")
    assert df["period_utc"].tolist() == [NAN_HOUR]


def test_local_time_handles_spring_forward(query):
    df = query(
        """
        select period_local from staging.stg_eia__hourly_demand
        where period_utc between '2024-03-10 07:00+00' and '2024-03-10 08:00+00'
        order by period_utc
        """
    )
    # 07:00 UTC is 01:00 CST; one hour later clocks jump to 03:00 CDT. 02:00 never happens.
    assert df["period_local"].dt.strftime("%H:%M").tolist() == ["01:00", "03:00"]


def test_local_time_repeats_at_fall_back(query):
    df = query(
        """
        select count(*) as n from staging.stg_eia__hourly_demand
        where cast(period_local as date) = '2024-11-03' and hour(period_local) = 1
        """
    )
    assert df["n"].item() == 2  # 01:00 CDT and 01:00 CST: two UTC hours, same local label


def test_forecast_grain_and_leads(query):
    df = query("select * from staging.stg_open_meteo__weather_forecast")
    assert len(df) == 24 * len(DAYS) * len(POINTS) * 2
    assert sorted(df["lead_days"].unique()) == [1, 2]
    assert not df.duplicated(["period_utc", "point", "lead_days"]).any()


def test_reanalysis_grain(query):
    df = query("select * from staging.stg_open_meteo__weather_reanalysis")
    assert len(df) == 24 * len(DAYS) * len(POINTS)
    assert "lead_days" not in df.columns
    assert isinstance(df["period_utc"].iloc[0], pd.Timestamp)
