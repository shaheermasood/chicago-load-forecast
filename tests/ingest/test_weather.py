"""Tests for the Open-Meteo loader. Fixtures copy the real response shape (list per location)."""

from datetime import UTC, date, datetime

import pandas as pd
import pytest
import responses

from chiload.ingest.store import partition_path
from chiload.ingest.weather import (
    SOURCES,
    Point,
    WeatherError,
    build_params,
    load_weather,
    parse_response,
)

POINTS = [Point("ohare", 41.9786, -87.9048), Point("midway", 41.7868, -87.7522)]
VARS = {"temperature_2m": "temp_f", "relative_humidity_2m": "rh_pct"}
UNITS = {"temperature_2m": "°F", "relative_humidity_2m": "%"}
T1 = datetime(2024, 7, 5, tzinfo=UTC)
T2 = datetime(2024, 7, 6, tzinfo=UTC)


def location(day: str, hours: int = 24, *, leads=(), temp=70.0, null_from=None):
    times = [f"{day}T{h:02d}:00" for h in range(hours)]
    hourly, units = {"time": times}, {"time": "iso8601"}
    for api, unit in UNITS.items():
        for key in [f"{api}_previous_day{n}" for n in leads] or [api]:
            vals = [temp + h for h in range(hours)]
            if null_from is not None:
                vals = [v if h < null_from else None for h, v in enumerate(vals)]
            hourly[key], units[key] = vals, unit
    return {
        "latitude": 41.98,
        "longitude": -87.89,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "hourly_units": units,
        "hourly": hourly,
    }


def test_build_params_previous_runs():
    p = build_params(POINTS, VARS, date(2024, 7, 1), date(2024, 7, 31), lead_days=[1, 2])
    assert p["latitude"] == "41.9786,41.7868"
    assert p["hourly"].split(",") == [
        "temperature_2m_previous_day1",
        "temperature_2m_previous_day2",
        "relative_humidity_2m_previous_day1",
        "relative_humidity_2m_previous_day2",
    ]
    assert (p["temperature_unit"], p["wind_speed_unit"], p["timezone"]) == (
        "fahrenheit",
        "mph",
        "GMT",
    )


def test_parse_long_format_with_leads():
    body = [location("2024-07-01", 2, leads=(1, 2)), location("2024-07-01", 2, leads=(1, 2))]
    df = parse_response(body, POINTS, VARS, lead_days=[1, 2])
    assert len(df) == 2 * 2 * 2  # points x leads x hours
    assert set(df.columns) == {"period_utc", "point", "lead_days", "temp_f", "rh_pct"}
    assert df["period_utc"].dt.tz is not None
    assert not df.duplicated(["period_utc", "point", "lead_days"]).any()


def test_parse_single_location_object():
    df = parse_response(location("2024-07-01", 3), POINTS[:1], VARS)
    assert df["point"].unique().tolist() == ["ohare"]
    assert "lead_days" not in df


def test_parse_rejects_wrong_units():
    body = location("2024-07-01", 1)
    body["hourly_units"]["temperature_2m"] = "°C"
    with pytest.raises(WeatherError, match="units"):
        parse_response(body, POINTS[:1], VARS)


def test_parse_rejects_error_and_location_mismatch():
    with pytest.raises(WeatherError, match="bad"):
        parse_response({"error": True, "reason": "bad"}, POINTS, VARS)
    with pytest.raises(WeatherError, match="locations"):
        parse_response([location("2024-07-01", 1)], POINTS, VARS)


def run(tmp_path, source="previous_runs", leads=(1, 2), fetched_at=T1, end=date(2024, 7, 1)):
    return load_weather(
        source,
        points=POINTS,
        variables=VARS,
        start=date(2024, 7, 1),
        end=end,
        root=tmp_path,
        lead_days=leads,
        fetched_at=fetched_at,
        backoff_seconds=0,
    )


@responses.activate
def test_load_is_idempotent(tmp_path):
    body = [location("2024-07-01", leads=(1, 2)) for _ in POINTS]
    for _ in range(2):
        responses.get(SOURCES["previous_runs"].url, json=body)

    assert [r.status for r in run(tmp_path)] == ["created"]
    path = partition_path(tmp_path, date(2024, 7, 1))
    before = path.read_bytes()
    assert [r.status for r in run(tmp_path, fetched_at=T2)] == ["unchanged"]
    assert path.read_bytes() == before
    assert len(pd.read_parquet(path)) == 24 * 2 * 2


@responses.activate
def test_all_null_hours_are_skipped_and_empty_days_reported(tmp_path):
    body = [location("2024-07-01", null_from=6) for _ in POINTS]
    responses.get(SOURCES["era5"].url, json=body)
    results = run(tmp_path, source="era5", leads=())
    assert results[0].rows == 6 * 2
    assert responses.calls[0].request.params["models"] == "era5"


@responses.activate
def test_daily_limit_is_not_retried(tmp_path):
    responses.get(
        SOURCES["era5"].url,
        status=429,
        json={"error": True, "reason": "Daily API request limit exceeded."},
    )
    with pytest.raises(WeatherError, match="Daily API request limit"):
        run(tmp_path, source="era5", leads=())
    assert len(responses.calls) == 1


def test_previous_runs_requires_leads(tmp_path):
    with pytest.raises(ValueError, match="lead_days"):
        run(tmp_path, leads=())
