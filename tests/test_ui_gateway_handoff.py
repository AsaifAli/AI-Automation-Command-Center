from __future__ import annotations

import json
from typing import ClassVar

import pytest

from ui import gateway_handoff
from ui.gateway_handoff import GatewayHandoffError, exchange_handoff_code


class FakeResponse:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {"content-length": "120"}

    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self.body


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def stream(self, method, endpoint, **kwargs):
        self.request = (method, endpoint, kwargs)
        return self.response


def test_exchange_requires_gateway_target_project(monkeypatch):
    response = FakeResponse({
        "access_token": "x" * 64,
        "expires_in": 600,
        "provider": "openai",
        "target_project": "flowpilot",
    })
    client = FakeClient(response)
    monkeypatch.setattr(gateway_handoff.httpx, "Client", lambda **kwargs: client)

    session = exchange_handoff_code("a" * 32, "https://gateway.example")

    assert session["provider"] == "openai"
    assert client.request[0] == "POST"
    assert client.request[1] == "https://gateway.example/v1/demo/session/exchange"
    assert client.request[2]["json"] == {"code": "a" * 32, "project": "flowpilot"}


def test_exchange_rejects_wrong_gateway_target_project(monkeypatch):
    response = FakeResponse({
        "access_token": "x" * 64,
        "expires_in": 600,
        "provider": "openai",
        "target_project": "quotesense",
    })
    monkeypatch.setattr(gateway_handoff.httpx, "Client", lambda **kwargs: FakeClient(response))

    with pytest.raises(GatewayHandoffError, match="different project"):
        exchange_handoff_code("a" * 32, "https://gateway.example")
