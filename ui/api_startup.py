"""Bounded startup retry for the separately deployed FlowPilot API service."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from typing import Any

import requests

RETRYABLE_HTTP_STATUSES = frozenset({502, 503, 504})


class APIStartupError(RuntimeError):
    """Sanitized error raised when the API does not become available."""


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _status_code_from_error(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        try:
            return int(status)
        except (TypeError, ValueError, OverflowError):
            return None

    match = re.search(r"\((\d{3})\)", str(exc))
    return int(match.group(1)) if match else None


def load_api_startup_data(
    api_get: Callable[[str], Any],
    response_json: Callable[[Any, str], Any],
    *,
    startup_timeout_seconds: int | None = None,
    retry_interval_seconds: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_retry: Callable[[int, float], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch initial health + metrics, retrying only transient startup failures.

    Connection errors and Render 502/503/504 responses are retried until the
    bounded startup deadline. Auth, routing and other client errors fail fast.
    """
    timeout_seconds = (
        startup_timeout_seconds
        if startup_timeout_seconds is not None
        else _bounded_env_int("API_STARTUP_TIMEOUT_SECONDS", 90, 10, 180)
    )
    retry_interval = (
        retry_interval_seconds
        if retry_interval_seconds is not None
        else _bounded_env_int("API_STARTUP_RETRY_INTERVAL_SECONDS", 4, 1, 10)
    )
    timeout_seconds = max(1, min(int(timeout_seconds), 180))
    retry_interval = max(1, min(int(retry_interval), 10))

    deadline = monotonic() + timeout_seconds
    attempt = 0

    while True:
        attempt += 1
        try:
            health = response_json(api_get("/health"), "Health check")
            metrics = response_json(api_get("/metrics"), "Metrics request")
            if not isinstance(health, dict) or not isinstance(metrics, dict):
                raise APIStartupError("The FlowPilot API returned an unexpected startup response.")
            return health, metrics
        except APIStartupError:
            raise
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as exc:
            status_code = _status_code_from_error(exc)
            retryable = (
                isinstance(exc, requests.RequestException)
                or status_code in RETRYABLE_HTTP_STATUSES
            )
            remaining = deadline - monotonic()

            if not retryable:
                if status_code is not None:
                    raise APIStartupError(
                        f"FlowPilot API startup check returned HTTP {status_code}. "
                        "Check the API URL, service access key and deployment configuration."
                    ) from None
                raise APIStartupError(
                    "FlowPilot API startup check failed. Check the API service configuration."
                ) from None

            if remaining <= 0:
                raise APIStartupError(
                    f"FlowPilot API did not become available within {timeout_seconds} seconds. "
                    "The Render service may still be waking; refresh the page shortly."
                ) from None

            wait_seconds = min(float(retry_interval), remaining)
            if on_retry is not None:
                on_retry(attempt, wait_seconds)
            sleep(wait_seconds)
