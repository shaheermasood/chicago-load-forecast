"""Minimal client for the EIA API v2.

Handles the three things every EIA call needs: the API key, retries on transient
failures, and pagination (the API returns at most 5,000 rows per request).

Docs: https://www.eia.gov/opendata/documentation.php
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.eia.gov/v2"
MAX_PAGE_SIZE = 5000
RETRY_STATUSES = {429, 500, 502, 503, 504}


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

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=query, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise EIAError(f"Network error calling {route}: {exc}") from exc
                self._sleep(attempt, reason=type(exc).__name__)
                continue

            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self._sleep(attempt, reason=f"HTTP {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise EIAError(f"HTTP {resp.status_code} from {route}: {resp.text[:300]}")

            body = resp.json()
            if "error" in body:
                raise EIAError(f"EIA error from {route}: {body['error']}")
            return body["response"]

        raise EIAError(f"Exhausted retries for {route}")  # pragma: no cover

    def iter_data(
        self,
        route: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int = MAX_PAGE_SIZE,
    ) -> Iterator[dict[str, Any]]:
        """Yield every row from a `/data` route, following offset pagination."""
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

    def _sleep(self, attempt: int, *, reason: str) -> None:
        delay = self.backoff_seconds * (2**attempt)
        log.warning("EIA request failed (%s); retrying in %.1fs", reason, delay)
        time.sleep(delay)
