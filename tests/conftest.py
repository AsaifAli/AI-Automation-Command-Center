"""Isolate tests from a developer's local or production .env configuration."""

from __future__ import annotations

import os

# API tests should run without credentials, while Settings validation tests
# must retain the production default that requires keys when auth is enabled.
os.environ.update(
    {
        "APP_ENV": "test",
        "API_AUTH_ENABLED": "false",
        "API_AUTH_REQUIRE_KEYS": "true",
        "API_KEY": "",
        "API_VIEWER_KEYS": "",
        "API_OPERATOR_KEYS": "",
        "API_REVIEWER_KEYS": "",
        "API_ADMIN_KEYS": "",
        # Keep unit/API tests offline; hosted telemetry is covered separately.
        "OTEL_ENABLED": "false",
    }
)
