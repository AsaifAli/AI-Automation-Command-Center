"""Secure, project-scoped exchange for one-time portfolio LLM handoff codes."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

PROJECT_SLUG = "flowpilot"
EXCHANGE_PATH = "/v1/demo/session/exchange"
MAX_HANDOFF_CODE_LENGTH = 512
MAX_RESPONSE_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_SESSION_SECONDS = 60 * 60

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,512}$")


class GatewayHandoffError(ValueError):
    """Raised when a one-time LLM Gateway handoff cannot be exchanged safely."""


def _exchange_endpoint(gateway_url: str) -> str:
    """Build the exact exchange endpoint from a configured Gateway base or URL."""
    configured = (gateway_url or "").strip()
    if not configured:
        raise GatewayHandoffError("LLM Gateway handoff is not configured.")

    try:
        parsed = urlsplit(configured)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise GatewayHandoffError("LLM Gateway endpoint is invalid.") from exc

    if (
        scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GatewayHandoffError("LLM Gateway endpoint is invalid.")

    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if scheme != "https" and hostname.lower() not in local_hosts:
        raise GatewayHandoffError("LLM Gateway endpoint must use HTTPS.")

    expected_port = 443 if scheme == "https" else 80
    if port not in {None, expected_port}:
        raise GatewayHandoffError("LLM Gateway endpoint uses an unsupported port.")

    path = parsed.path.rstrip("/")
    if path and path != EXCHANGE_PATH:
        raise GatewayHandoffError("LLM Gateway endpoint path is invalid.")

    endpoint_path = EXCHANGE_PATH
    return urlunsplit((scheme, parsed.netloc, endpoint_path, "", ""))


def exchange_handoff_code(code: str, gateway_url: str) -> dict[str, object]:
    """Exchange a single-use code for a short-lived, project-bound bearer token.

    The code is only sent in the POST body. Redirects are rejected and the
    response is bounded before JSON parsing. Secrets and response bodies are
    deliberately excluded from exceptions.
    """
    handoff_code = (code or "").strip()
    if not handoff_code or len(handoff_code) > MAX_HANDOFF_CODE_LENGTH:
        raise GatewayHandoffError("The Gateway handoff code is missing or invalid.")
    if not _CODE_PATTERN.fullmatch(handoff_code):
        raise GatewayHandoffError("The Gateway handoff code is invalid.")

    endpoint = _exchange_endpoint(gateway_url)
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    chunks: list[bytes] = []
    received_bytes = 0

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client, client.stream(
            "POST",
            endpoint,
            json={"code": handoff_code, "project": PROJECT_SLUG},
            headers={"Accept": "application/json"},
        ) as response:
            if 300 <= response.status_code < 400:
                raise GatewayHandoffError("Gateway handoff redirects are not allowed.")

            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise GatewayHandoffError("Gateway returned an invalid response.") from exc
                if declared_size < 0 or declared_size > MAX_RESPONSE_BYTES:
                    raise GatewayHandoffError("Gateway response exceeded the allowed size.")

            for chunk in response.iter_bytes():
                received_bytes += len(chunk)
                if received_bytes > MAX_RESPONSE_BYTES:
                    raise GatewayHandoffError("Gateway response exceeded the allowed size.")
                chunks.append(chunk)
    except GatewayHandoffError:
        raise
    except httpx.HTTPError as exc:
        raise GatewayHandoffError("Unable to exchange the Gateway handoff code.") from exc

    try:
        payload = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GatewayHandoffError("Gateway returned an invalid response.") from exc

    if not isinstance(payload, dict):
        raise GatewayHandoffError("Gateway returned an invalid response.")

    returned_project = payload.get("target_project")
    if returned_project != PROJECT_SLUG:
        raise GatewayHandoffError("Gateway session belongs to a different project.")

    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token.strip()
        or len(access_token) > 8192
        or not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or not 1 <= expires_in <= MAX_SESSION_SECONDS
    ):
        raise GatewayHandoffError("Gateway returned an invalid session.")

    provider = payload.get("provider", "")
    if not isinstance(provider, str):
        provider = ""

    return {
        "access_token": access_token.strip(),
        "expires_in": expires_in,
        "provider": provider[:80],
    }
