"""Shared HTTP helper: GET JSON with retries on transient failures.

Both the EIA and Open-Meteo loaders go through here so retry and error behaviour is the same
everywhere. Secrets passed in `redact` are scrubbed from every error message and log line,
because `requests` exceptions embed the full URL, query string and all.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class HTTPFetchError(RuntimeError):
    """Raised when a request fails and retrying won't help (or retries ran out)."""


def _scrub(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def get_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = 4,
    backoff_seconds: float = 1.0,
    timeout: float = 30.0,
    redact: Iterable[str] = (),
    error_cls: type[RuntimeError] = HTTPFetchError,
) -> Any:
    """GET `url` and return parsed JSON, retrying network errors and 429/5xx responses.

    Backoff doubles each attempt: backoff_seconds * 2**attempt.
    """
    secrets = tuple(redact)
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == max_retries:
                raise error_cls(_scrub(f"Network error calling {url}: {exc}", secrets)) from None
            _sleep(attempt, backoff_seconds, type(exc).__name__)
            continue

        if resp.status_code in RETRY_STATUSES and attempt < max_retries:
            _sleep(attempt, backoff_seconds, f"HTTP {resp.status_code}")
            continue
        if resp.status_code != 200:
            body = _scrub(resp.text[:300], secrets)
            raise error_cls(f"HTTP {resp.status_code} from {url}: {body}")
        try:
            return resp.json()
        except ValueError as exc:
            raise error_cls(f"Non-JSON response from {url}") from exc

    raise error_cls(f"Exhausted retries for {url}")  # pragma: no cover


def _sleep(attempt: int, backoff_seconds: float, reason: str) -> None:
    delay = backoff_seconds * (2**attempt)
    log.warning("Request failed (%s); retrying in %.1fs", reason, delay)
    time.sleep(delay)
