"""Backward-compatible import surface for the canonical UI Gateway handoff.

The Streamlit entry point imports modules from the ``ui`` package. Keeping the
implementation there avoids an ``app.py`` import-path collision while this
module preserves the existing backend and test import path.
"""

import httpx

from ui.gateway_handoff import (
    EXCHANGE_PATH,
    MAX_HANDOFF_CODE_LENGTH,
    MAX_RESPONSE_BYTES,
    MAX_SESSION_SECONDS,
    PROJECT_SLUG,
    REQUEST_TIMEOUT_SECONDS,
    GatewayHandoffError,
    _exchange_endpoint,
    exchange_handoff_code,
)

__all__ = [
    "EXCHANGE_PATH",
    "MAX_HANDOFF_CODE_LENGTH",
    "MAX_RESPONSE_BYTES",
    "MAX_SESSION_SECONDS",
    "PROJECT_SLUG",
    "REQUEST_TIMEOUT_SECONDS",
    "GatewayHandoffError",
    "_exchange_endpoint",
    "exchange_handoff_code",
    "httpx",
]
