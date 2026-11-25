"""EIA API v2 client and the hourly ComEd demand loader.

The client handles the three things every EIA call needs: the API key, retries on transient
failures, and pagination (the API returns at most 5,000 rows per request). `load_demand`
fetches EIA-930 subregion demand and writes it into idempotent daily Parquet partitions
(see `chiload.ingest.store`).

Docs: https://www.eia.gov/opendata/documentation.php
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from chiload.ingest.dates import days, month_chunks
from chiload.ingest.http import get_json
from chiload.ingest.store import WriteResult, write_partition

log = logging.getLogger(__name__)

BASE_URL = "https://api.eia.gov/v2"
MAX_PAGE_SIZE = 5000


class EIAError(RuntimeError):
    """Raised when the EIA API returns an error we can't recover from."""


class EIAClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout = timeout
        self.session = session or requests.Session()

    def get(self, route: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a route and return the parsed `response` object, retrying transient errors."""
        url = f"{self.base_url}/{route.strip('/')}"
        query = {**(params or {}), "api_key": self.api_key}
        body = get_json(
            self.session,
            url,
            query,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
            timeout=self.timeout,
            redact=[self.api_key],
            error_cls=EIAError,
        )
        if "error" in body:
            raise EIAError(f"EIA error from {route}: {body['error']}")
        return body["response"]

    def iter_data(
        self,
        route: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int = MAX_PAGE_SIZE,
    ) -> Iterator[dict[str, Any]]:
        """Yield every row from a `/data` route, following offset pagination.

        Pass a `sort[...]` param when results can span pages: without an explicit sort the
        API doesn't promise a stable order, so offset paging could skip or repeat rows.
        """
        if not 0 < page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        data_route = route if route.rstrip("/").endswith("/data") else f"{route}/data"
        offset = 0
        while True:
            page = self.get(data_route, {**(params or {}), "offset": offset, "length": page_size})
            rows = page.get("data", [])
            yield from rows
            total = int(page.get("total", 0))
            offset += len(rows)
            if not rows or offset >= total:
                break

    def facet_values(self, route: str, facet: str) -> list[dict[str, str]]:
        """Return the available values ({'id', 'name'}) for a facet on a route."""
        return self.get(f"{route}/facet/{facet}").get("facets", [])


# --- Hourly subregion demand loader -------------------------------------------------------

#: EIA reports hourly demand as energy over the hour; for a 1-hour interval MWh == average MW.
EXPECTED_UNITS = "megawatthours"
DEMAND_KEYS = ["period_utc"]
_DEMAND_DTYPES = {
    "period_utc": "datetime64[us, UTC]",
    "parent": "str",
    "subba": "str",
    "load_mw": "float64",
}


def demand_params(parent: str, subregion: str, start: date, end: date) -> dict[str, Any]:
    """Query params for hourly demand of one subregion over whole UTC days [start, end]."""
    return {
        "frequency": "hourly",  # UTC hours; "local-hourly" would bring back DST headaches
        "data[]": "value",
        "facets[parent][]": parent,
        "facets[subba][]": subregion,
        "start": f"{start.isoformat()}T00",
        "end": f"{end.isoformat()}T23",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }


def normalize_demand(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Turn raw API rows into period_utc, parent, subba, load_mw.

    Missing or non-numeric values stay as NaN and aren't dropped, so later stages can flag them.
    """
    cols = ["period_utc", "parent", "subba", "load_mw"]
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=t) for c, t in _DEMAND_DTYPES.items()})[cols]

    raw = pd.DataFrame(rows)
    units = set(raw.get("value-units", pd.Series(dtype=str)).dropna().str.lower())
    if units - {EXPECTED_UNITS}:
        raise EIAError(f"Unexpected units {sorted(units)}; expected {EXPECTED_UNITS}")

    df = pd.DataFrame(
        {
            "period_utc": pd.to_datetime(raw["period"], format="%Y-%m-%dT%H", utc=True),
            "parent": raw["parent"].astype(str),
            "subba": raw["subba"].astype(str),
            "load_mw": pd.to_numeric(raw["value"], errors="coerce").astype("float64"),
        }
    )

    dupes = df.duplicated(DEMAND_KEYS, keep=False)
    if dupes.any():
        conflicting = df[dupes].groupby("period_utc")["load_mw"].nunique(dropna=False).gt(1)
        log.warning(
            "EIA returned %d duplicate hours (%d with conflicting values); keeping the last",
            int(dupes.sum()),
            int(conflicting.sum()),
        )
        df = df.drop_duplicates(DEMAND_KEYS, keep="last")

    return df.sort_values("period_utc").reset_index(drop=True)


def fetch_demand(
    client: EIAClient, *, route: str, parent: str, subregion: str, start: date, end: date
) -> pd.DataFrame:
    """Fetch hourly demand for one subregion over whole UTC days [start, end]."""
    rows = list(client.iter_data(route, demand_params(parent, subregion, start, end)))
    return normalize_demand(rows)


def load_demand(
    client: EIAClient,
    *,
    route: str,
    parent: str,
    subregion: str,
    start: date,
    end: date,
    root: Path,
    fetched_at: datetime | None = None,
) -> list[WriteResult]:
    """Fetch [start, end] month by month and write one partition per UTC day.

    A day with no rows is reported as "missing" and any partition already stored for it is
    left alone, so an EIA outage can't wipe out good data.
    """
    fetched_at = fetched_at or datetime.now(UTC)
    results: list[WriteResult] = []
    for chunk_start, chunk_end in month_chunks(start, end):
        df = fetch_demand(
            client,
            route=route,
            parent=parent,
            subregion=subregion,
            start=chunk_start,
            end=chunk_end,
        )
        by_day = dict(tuple(df.groupby(df["period_utc"].dt.date)))
        for day in days(chunk_start, chunk_end):
            rows = by_day.get(day)
            if rows is None or rows.empty:
                results.append(WriteResult(day, "missing"))
                continue
            results.append(
                write_partition(rows, root, day, key_cols=DEMAND_KEYS, fetched_at=fetched_at)
            )
        log.info("EIA %s..%s: %d rows", chunk_start, chunk_end, len(df))
    return results
