"""Bounded retries for safe API reads used by the Streamlit dashboard."""

from __future__ import annotations

import email.utils
import time
from collections.abc import Callable
from datetime import UTC, datetime

import requests

RETRYABLE_GET_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_delay(response: requests.Response, attempt: int, max_delay: float) -> float:
    """Honor Retry-After when possible, with a strict upper bound."""
    raw = response.headers.get("Retry-After", "").strip()
    delay: float | None = None
    if raw:
        try:
            delay = float(raw)
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                delay = (retry_at - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = None
    if delay is None:
        delay = min(0.5 * (2**attempt), max_delay)
    return min(max(delay, 0.1), max_delay)


def resilient_get(
    request: Callable[[], requests.Response],
    *,
    max_attempts: int = 3,
    max_delay_seconds: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """Retry only idempotent GETs for transient network and server failures.

    The final response is returned to the caller so normal response handling can
    render a useful status-specific message. Authentication and not-found
    responses are never retried.
    """
    attempts = max(1, min(int(max_attempts), 4))
    last_response: requests.Response | None = None
    for attempt in range(attempts):
        try:
            response = request()
        except requests.RequestException:
            if attempt + 1 >= attempts:
                raise
            sleep(min(0.5 * (2**attempt), max_delay_seconds))
            continue

        last_response = response
        if response.status_code not in RETRYABLE_GET_STATUSES or attempt + 1 >= attempts:
            return response
        sleep(_retry_delay(response, attempt, max_delay_seconds))

    # Defensive fallback; all normal paths return or raise above.
    if last_response is not None:
        return last_response
    raise RuntimeError("API read did not produce a response")
