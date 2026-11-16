"""Small, testable API-header builder for the FlowPilot Streamlit UI."""

from __future__ import annotations

import os
import time


def build_api_headers(
    api_key: str = "",
    gateway_token: str = "",
    gateway_expires_at: float | None = None,
    *,
    now: float | None = None,
) -> dict[str, str]:
    """Build authenticated headers without putting session tokens in URLs.

    The gateway token is attached only when a valid expiry timestamp is also
    present. API authorization remains independent from model-provider access.
    """
    headers: dict[str, str] = {}
    if api_key.strip():
        headers["X-API-Key"] = api_key.strip()

    if gateway_token.strip() and gateway_expires_at is not None:
        current_time = time.time() if now is None else now
        try:
            active = float(gateway_expires_at) > current_time
        except (TypeError, ValueError, OverflowError):
            active = False
        if active:
            headers["X-LLM-Gateway-Token"] = gateway_token.strip()

    return headers

def api_timeout_for(path: str, *, run_timeout_seconds: int | str | None = None) -> int:
    """Return a bounded HTTP timeout for the inline workflow-create request.

    Normal UI reads/writes remain short. The public Render demo executes a
    workflow inline, so POST /api/v1/runs must wait longer than a routine API
    call while the LLM completes.
    """
    route = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if route != "/api/v1/runs":
        return 15

    raw_value = run_timeout_seconds
    if raw_value is None:
        raw_value = os.getenv("RUN_REQUEST_TIMEOUT_SECONDS", "75")
    try:
        timeout = int(raw_value)
    except (TypeError, ValueError, OverflowError):
        timeout = 75
    return max(30, min(timeout, 180))
