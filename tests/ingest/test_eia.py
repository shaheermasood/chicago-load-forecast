"""Tests for the EIA client. All HTTP is mocked; no API key or network needed."""

import pytest
import requests
import responses

from chiload.ingest.eia import BASE_URL, EIAClient, EIAError

ROUTE = "electricity/rto/region-sub-ba-data"


@pytest.fixture
def client():
    return EIAClient("test-key", backoff_seconds=0)


@responses.activate
def test_get_returns_response_and_sends_key(client):
    responses.get(f"{BASE_URL}/{ROUTE}", json={"response": {"total": "0", "data": []}})
    assert client.get(ROUTE) == {"total": "0", "data": []}
    assert "api_key=test-key" in responses.calls[0].request.url


@responses.activate
def test_retries_transient_errors_then_succeeds(client):
    url = f"{BASE_URL}/{ROUTE}"
    responses.get(url, status=503)
    responses.get(url, status=429)
    responses.get(url, json={"response": {"ok": True}})
    assert client.get(ROUTE) == {"ok": True}
    assert len(responses.calls) == 3


@responses.activate
def test_retries_network_errors(client):
    url = f"{BASE_URL}/{ROUTE}"
    responses.get(url, body=requests.ConnectionError("boom"))
    responses.get(url, json={"response": {"ok": True}})
    assert client.get(ROUTE) == {"ok": True}


@responses.activate
def test_gives_up_after_max_retries():
    client = EIAClient("k", max_retries=2, backoff_seconds=0)
    for _ in range(3):
        responses.get(f"{BASE_URL}/{ROUTE}", status=500)
    with pytest.raises(EIAError, match="HTTP 500"):
        client.get(ROUTE)
    assert len(responses.calls) == 3


@responses.activate
def test_does_not_retry_client_errors(client):
    responses.get(f"{BASE_URL}/{ROUTE}", status=403, body="invalid key")
    with pytest.raises(EIAError, match="403"):
        client.get(ROUTE)
    assert len(responses.calls) == 1


@responses.activate
def test_error_in_body_raises(client):
    responses.get(f"{BASE_URL}/{ROUTE}", json={"error": "Invalid facet"})
    with pytest.raises(EIAError, match="Invalid facet"):
        client.get(ROUTE)


@responses.activate
def test_iter_data_follows_pagination(client):
    url = f"{BASE_URL}/{ROUTE}/data"
    pages = [
        {"total": "5", "data": [{"i": 0}, {"i": 1}]},
        {"total": "5", "data": [{"i": 2}, {"i": 3}]},
        {"total": "5", "data": [{"i": 4}]},
    ]
    for p in pages:
        responses.get(url, json={"response": p})

    rows = list(client.iter_data(ROUTE, {"frequency": "hourly"}, page_size=2))

    assert [r["i"] for r in rows] == [0, 1, 2, 3, 4]
    offsets = [c.request.params["offset"] for c in responses.calls]
    assert offsets == ["0", "2", "4"]


@responses.activate
def test_iter_data_stops_on_empty_page(client):
    responses.get(f"{BASE_URL}/{ROUTE}/data", json={"response": {"total": "100", "data": []}})
    assert list(client.iter_data(ROUTE)) == []
    assert len(responses.calls) == 1


def test_iter_data_rejects_bad_page_size(client):
    with pytest.raises(ValueError):
        next(client.iter_data(ROUTE, page_size=10_000))


@responses.activate
def test_facet_values(client):
    facets = [{"id": "ZONA", "name": "Zone A"}]
    responses.get(f"{BASE_URL}/{ROUTE}/facet/subba", json={"response": {"facets": facets}})
    assert client.facet_values(ROUTE, "subba") == facets


@responses.activate
def test_errors_never_contain_the_api_key():
    client = EIAClient("SECRET-KEY-123", max_retries=0, backoff_seconds=0)
    responses.get(
        f"{BASE_URL}/{ROUTE}", body=requests.ConnectionError("url=?api_key=SECRET-KEY-123")
    )
    with pytest.raises(EIAError) as excinfo:
        client.get(ROUTE)
    assert "SECRET-KEY-123" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


@responses.activate
def test_error_body_echoing_key_is_redacted():
    client = EIAClient("SECRET-KEY-123", backoff_seconds=0)
    responses.get(f"{BASE_URL}/{ROUTE}", status=403, body="bad key SECRET-KEY-123")
    with pytest.raises(EIAError) as excinfo:
        client.get(ROUTE)
    assert "SECRET-KEY-123" not in str(excinfo.value)
