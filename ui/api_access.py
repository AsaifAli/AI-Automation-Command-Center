"""Small, testable API-header builder for the FlowPilot Streamlit UI."""

from __future__ import annotations

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
