"""Hourly weather for the ComEd weather points from Open-Meteo (no API key needed).

Sources (see docs/decisions/0003-weather-from-previous-runs-api.md):

* ``previous_runs``: the Previous Runs API. ``temperature_2m_previous_day1`` is the value
  forecast by the model run one day earlier. This matches "the forecast as it stood when we
  issued ours", which is what the model gets at 10:00 the day before. Stored with a
  ``lead_days`` column.
* ``historical_forecast``: the Historical Forecast API, built from the first hours of each run.
  It's close to a 0-day lead, so it understates real day-ahead forecast error. Supported but off
  by default.
* ``era5``: ERA5 reanalysis through the Historical Weather API. It's "what actually happened",
  used for the oracle ablation and climatology.

All points go in one request per month. Times are requested in GMT and stored as
``period_utc``. Units are requested explicitly and checked against the response.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from chiload.ingest.dates import days, month_chunks
from chiload.ingest.http import get_json
from chiload.ingest.store import WriteResult, write_partition

log = logging.getLogger(__name__)


class WeatherError(RuntimeError):
    """Raised when Open-Meteo returns an error or something unexpected."""


@dataclass(frozen=True)
class Source:
    url: str
    extra_params: dict[str, str] = field(default_factory=dict)
    has_lead: bool = False


SOURCES: dict[str, Source] = {
    "previous_runs": Source("https://previous-runs-api.open-meteo.com/v1/forecast", has_lead=True),
    "historical_forecast": Source("https://historical-forecast-api.open-meteo.com/v1/forecast"),
    "era5": Source("https://archive-api.open-meteo.com/v1/archive", {"models": "era5"}),
}

#: Units we request, keyed by the suffix of our column name. Checked against `hourly_units`.
UNIT_PARAMS = {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "GMT"}
EXPECTED_UNITS = {"_f": "°F", "_mph": "mp/h", "_pct": "%"}


@dataclass(frozen=True)
class Point:
    name: str
    lat: float
    lon: float


def build_params(
    points: Sequence[Point],
    variables: dict[str, str],
    start: date,
    end: date,
    *,
    lead_days: Sequence[int] = (),
) -> dict[str, Any]:
    if lead_days:
        hourly = [f"{v}_previous_day{n}" for v in variables for n in lead_days]
    else:
        hourly = list(variables)
    return {
        "latitude": ",".join(f"{p.lat:.4f}" for p in points),
        "longitude": ",".join(f"{p.lon:.4f}" for p in points),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(hourly),
        **UNIT_PARAMS,
    }


def parse_response(
    body: Any,
    points: Sequence[Point],
    variables: dict[str, str],
    *,
    lead_days: Sequence[int] = (),
) -> pd.DataFrame:
    """Turn an Open-Meteo response into long format: one row per (period_utc, point[, lead]).

    Open-Meteo returns a list with one object per location, in request order. A single location
    comes back as a bare object instead of a list.
    """
    if isinstance(body, dict) and body.get("error"):
        raise WeatherError(f"Open-Meteo error: {body.get('reason')}")
    locations = body if isinstance(body, list) else [body]
    if len(locations) != len(points):
        raise WeatherError(f"Expected {len(points)} locations, got {len(locations)}")

    frames = []
    for point, loc in zip(points, locations, strict=True):
        hourly, units = loc["hourly"], loc.get("hourly_units", {})
        period = pd.to_datetime(pd.Series(hourly["time"]), format="%Y-%m-%dT%H:%M", utc=True)
        for lead in lead_days or [None]:
            cols: dict[str, Any] = {"period_utc": period, "point": point.name}
            if lead is not None:
                cols["lead_days"] = lead
            for api_name, our_name in variables.items():
                key = api_name if lead is None else f"{api_name}_previous_day{lead}"
                if key not in hourly:
                    raise WeatherError(f"Response is missing variable {key!r}")
                _check_units(key, our_name, units.get(key))
                cols[our_name] = pd.to_numeric(pd.Series(hourly[key]), errors="coerce")
            frames.append(pd.DataFrame(cols))

    df = pd.concat(frames, ignore_index=True)
    for our_name in variables.values():
        df[our_name] = df[our_name].astype("float64")
    if "lead_days" in df:
        df["lead_days"] = df["lead_days"].astype("int64")
    return df


def _check_units(key: str, our_name: str, got: str | None) -> None:
    for suffix, expected in EXPECTED_UNITS.items():
        if our_name.endswith(suffix):
            if got != expected:
                raise WeatherError(f"{key}: expected units {expected!r}, got {got!r}")
            return


def load_weather(
    source_name: str,
    *,
    points: Sequence[Point],
    variables: dict[str, str],
    start: date,
    end: date,
    root: Path,
    lead_days: Sequence[int] = (),
    session: requests.Session | None = None,
    fetched_at: datetime | None = None,
    max_retries: int = 4,
    backoff_seconds: float = 2.0,
) -> list[WriteResult]:
    """Fetch one source month by month and write one partition per UTC day.

    Days where every value is null (e.g. before a source's archive begins) are reported as
    "missing" and not written.
    """
    source = SOURCES[source_name]
    if source.has_lead and not lead_days:
        raise ValueError(f"{source_name} needs lead_days")
    if not source.has_lead:
        lead_days = ()

    session = session or requests.Session()
    fetched_at = fetched_at or datetime.now(UTC)
    key_cols = ["period_utc", "point"] + (["lead_days"] if lead_days else [])
    value_cols = list(variables.values())

    results: list[WriteResult] = []
    for chunk_start, chunk_end in month_chunks(start, end):
        params = {
            **build_params(points, variables, chunk_start, chunk_end, lead_days=lead_days),
            **source.extra_params,
        }
        body = get_json(
            session,
            source.url,
            params,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            give_up_if=["Daily API request limit"],
            error_cls=WeatherError,
        )
        df = parse_response(body, points, variables, lead_days=lead_days)
        df = df[df[value_cols].notna().any(axis=1)]
        by_day = dict(tuple(df.groupby(df["period_utc"].dt.date)))
        for day in days(chunk_start, chunk_end):
            rows = by_day.get(day)
            if rows is None or rows.empty:
                results.append(WriteResult(day, "missing"))
                continue
            results.append(
                write_partition(rows, root, day, key_cols=key_cols, fetched_at=fetched_at)
            )
        log.info("%s %s..%s: %d rows", source_name, chunk_start, chunk_end, len(df))
    return results
