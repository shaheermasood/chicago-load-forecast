"""Tests for the hourly demand loader. HTTP is mocked; rows mirror the EIA v2 response shape."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
import responses

from chiload.ingest.eia import (
    BASE_URL,
    EIAClient,
    EIAError,
    demand_params,
    load_demand,
    normalize_demand,
)
from chiload.ingest.store import existing_days, partition_path

ROUTE = "electricity/rto/region-sub-ba-data"
DATA_URL = f"{BASE_URL}/{ROUTE}/data"
T1 = datetime(2024, 7, 5, tzinfo=UTC)
T2 = datetime(2024, 7, 6, tzinfo=UTC)


def eia_rows(start: str, n: int, value=10_000):
    t0 = datetime.fromisoformat(start)
    return [
        {
            "period": (t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H"),
            "subba": "CE",
            "subba-name": "Commonwealth Edison",
            "parent": "PJM",
            "parent-name": "PJM Interconnection, LLC",
            "value": str(value + i) if isinstance(value, int) else value,
            "value-units": "megawatthours",
        }
        for i in range(n)
    ]


def page(rows):
    return {"response": {"total": str(len(rows)), "data": rows}}


@pytest.fixture
def client():
    return EIAClient("k", backoff_seconds=0)


def load(client, root, start, end, fetched_at=T1):
    return load_demand(
        client,
        route=ROUTE,
        parent="PJM",
        subregion="CE",
        start=start,
        end=end,
        root=root,
        fetched_at=fetched_at,
    )


def test_params_cover_whole_utc_days_and_sort_for_stable_paging():
    p = demand_params("PJM", "CE", date(2024, 7, 1), date(2024, 7, 31))
    assert p["start"] == "2024-07-01T00"
    assert p["end"] == "2024-07-31T23"
    assert p["frequency"] == "hourly"
    assert p["sort[0][column]"] == "period"
    assert p["facets[subba][]"] == "CE"


def test_normalize_parses_utc_and_numeric_values():
    rows = eia_rows("2024-07-01T00", 2)
    rows[1]["value"] = None
    df = normalize_demand(rows)
    assert list(df.columns) == ["period_utc", "parent", "subba", "load_mw"]
    assert df["period_utc"].iloc[0] == pd.Timestamp("2024-07-01T00", tz="UTC")
    assert df["load_mw"].iloc[0] == 10_000.0
    assert pd.isna(df["load_mw"].iloc[1])  # kept, not dropped


def test_normalize_rejects_unexpected_units():
    rows = eia_rows("2024-07-01T00", 1)
    rows[0]["value-units"] = "megawatts"
    with pytest.raises(EIAError, match="units"):
        normalize_demand(rows)


def test_normalize_dedupes_repeated_hours():
    rows = eia_rows("2024-07-01T00", 2) + eia_rows("2024-07-01T01", 1, value=99)
    df = normalize_demand(rows)
    assert len(df) == 2
    assert df["load_mw"].tolist() == [10_000.0, 99.0]


def test_normalize_empty():
    assert normalize_demand([]).empty


@responses.activate
def test_load_writes_one_partition_per_utc_day(client, tmp_path):
    responses.get(DATA_URL, json=page(eia_rows("2024-07-01T00", 48)))
    results = load(client, tmp_path, date(2024, 7, 1), date(2024, 7, 2))

    assert [r.status for r in results] == ["created", "created"]
    assert existing_days(tmp_path) == [date(2024, 7, 1), date(2024, 7, 2)]
    day2 = pd.read_parquet(partition_path(tmp_path, date(2024, 7, 2)))
    assert len(day2) == 24
    assert day2["period_utc"].min() == pd.Timestamp("2024-07-02T00", tz="UTC")


@responses.activate
def test_second_run_changes_nothing(client, tmp_path):
    for _ in range(2):
        responses.get(DATA_URL, json=page(eia_rows("2024-07-01T00", 24)))
    load(client, tmp_path, date(2024, 7, 1), date(2024, 7, 1), fetched_at=T1)
    path = partition_path(tmp_path, date(2024, 7, 1))
    before = path.read_bytes()

    results = load(client, tmp_path, date(2024, 7, 1), date(2024, 7, 1), fetched_at=T2)

    assert [r.status for r in results] == ["unchanged"]
    assert path.read_bytes() == before


@responses.activate
def test_requests_month_by_month(client, tmp_path):
    responses.get(DATA_URL, json=page(eia_rows("2024-01-31T00", 24)))
    responses.get(DATA_URL, json=page(eia_rows("2024-02-01T00", 24)))
    load(client, tmp_path, date(2024, 1, 31), date(2024, 2, 1))
    starts = [c.request.params["start"] for c in responses.calls]
    assert starts == ["2024-01-31T00", "2024-02-01T00"]


@responses.activate
def test_empty_day_is_reported_and_does_not_delete_stored_data(client, tmp_path):
    responses.get(DATA_URL, json=page(eia_rows("2024-07-01T00", 24)))
    responses.get(DATA_URL, json=page([]))
    load(client, tmp_path, date(2024, 7, 1), date(2024, 7, 1))

    results = load(client, tmp_path, date(2024, 7, 1), date(2024, 7, 1), fetched_at=T2)

    assert [r.status for r in results] == ["missing"]
    assert len(pd.read_parquet(partition_path(tmp_path, date(2024, 7, 1)))) == 24
