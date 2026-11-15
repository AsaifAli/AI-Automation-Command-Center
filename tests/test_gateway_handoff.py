from __future__ import annotations

import json

import pytest

from app.gateway_handoff import (
    EXCHANGE_PATH,
    PROJECT_SLUG,
    GatewayHandoffError,
    _exchange_endpoint,
    exchange_handoff_code,
)


class FakeResponse:
    def __init__(self, *, status_code: int = 200, body: bytes = b"{}", headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "https://gateway.example/v1/demo/session/exchange")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def iter_bytes(self):
        yield self._body


class FakeClient:
    response = FakeResponse()
    last_request = None

    def __init__(self, **kwargs):
        self.options = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, method, url, **kwargs):
        self.last_request = (method, url, kwargs)
        return self.response


def test_exchange_endpoint_accepts_gateway_base_url():
    assert _exchange_endpoint("https://gateway.example") == (
        f"https://gateway.example{EXCHANGE_PATH}"
    )


def test_exchange_endpoint_rejects_insecure_remote_url():
    with pytest.raises(GatewayHandoffError):
        _exchange_endpoint("http://gateway.example")


def test_exchange_endpoint_rejects_query_and_credentials():
    for url in (
        "https://user:pass@gateway.example",
        "https://gateway.example?token=secret",
    ):
        with pytest.raises(GatewayHandoffError):
            _exchange_endpoint(url)


def test_exchange_posts_project_scoped_code_and_validates_response(monkeypatch):
    # Request uses `project`; the Gateway response binds the session through
    # `target_project`. Keep the two sides of the contract explicit in tests.
    result = {
        "access_token": "short-lived-token",
        "expires_in": 600,
        "provider": "openrouter",
        "target_project": PROJECT_SLUG,
    }
    created_clients = []

    def client_factory(**kwargs):
        fake_client = FakeClient(**kwargs)
        fake_client.response = FakeResponse(body=json.dumps(result).encode("utf-8"))
        created_clients.append(fake_client)
        return fake_client

    monkeypatch.setattr("app.gateway_handoff.httpx.Client", client_factory)

    exchanged = exchange_handoff_code(
        "A" * 32,
        "https://gateway.example",
    )

    assert exchanged["access_token"] == "short-lived-token"
    assert exchanged["expires_in"] == 600
    fake = created_clients[0]
    method, endpoint, request = fake.last_request
    assert method == "POST"
    assert endpoint.endswith(EXCHANGE_PATH)
    assert request["json"] == {"code": "A" * 32, "project": PROJECT_SLUG}
    assert fake.options["follow_redirects"] is False
    assert fake.options["trust_env"] is False


def test_exchange_rejects_wrong_project(monkeypatch):
    fake = FakeClient()
    fake.response = FakeResponse(
        body=json.dumps({
            "access_token": "short-lived-token",
            "expires_in": 600,
            "target_project": "quotesense",
        }).encode("utf-8")
    )
    monkeypatch.setattr("app.gateway_handoff.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GatewayHandoffError, match="different project"):
        exchange_handoff_code("A" * 32, "https://gateway.example")


def test_exchange_rejects_large_response(monkeypatch):
    fake = FakeClient()
    fake.response = FakeResponse(
        body=b"x" * (64 * 1024 + 1),
    )
    monkeypatch.setattr("app.gateway_handoff.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GatewayHandoffError, match="size"):
        exchange_handoff_code("A" * 32, "https://gateway.example")


def test_exchange_rejects_redirect(monkeypatch):
    fake = FakeClient()
    fake.response = FakeResponse(
        status_code=302,
        headers={"location": "https://elsewhere.example"},
    )
    monkeypatch.setattr("app.gateway_handoff.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GatewayHandoffError, match="redirects"):
        exchange_handoff_code("A" * 32, "https://gateway.example")


def test_exchange_rejects_invalid_handoff_code_before_network(monkeypatch):
    called = False

    def unexpected_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called for invalid code")

    monkeypatch.setattr("app.gateway_handoff.httpx.Client", unexpected_client)
    with pytest.raises(GatewayHandoffError):
        exchange_handoff_code("not valid", "https://gateway.example")
    assert called is False
