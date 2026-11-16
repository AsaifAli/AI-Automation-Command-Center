from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.api.main as api_module


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload
        self.content = b"status-payload"

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse, observed: dict):
        self.response = response
        self.observed = observed

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url: str, *, headers: dict):
        self.observed["url"] = url
        self.observed["headers"] = headers
        return self.response


def _install_status_response(monkeypatch, payload: dict):
    observed: dict = {}
    monkeypatch.setattr(api_module.settings, "llm_gateway_url", "https://gateway.example/v1")
    monkeypatch.setattr(api_module.settings, "llm_base_url", "")
    monkeypatch.setattr(api_module.time, "time", lambda: 1000)
    monkeypatch.setattr(
        api_module.httpx,
        "Client",
        lambda **_kwargs: FakeClient(FakeResponse(payload), observed),
    )
    return observed


def test_gateway_status_contract_accepts_flowpilot_delegated_session(monkeypatch):
    observed = _install_status_response(
        monkeypatch,
        {
            "active": True,
            "project": "flowpilot",
            "session_project": "portfolio",
            "expires_at": 1700,
        },
    )

    remaining = api_module.verify_flowpilot_gateway_session("gateway-bearer")

    assert remaining == 698
    assert observed["url"] == "https://gateway.example/v1/demo/session/status"
    assert observed["headers"] == {"Authorization": "Bearer gateway-bearer"}


def test_gateway_status_rejects_wrong_demo_project(monkeypatch):
    _install_status_response(
        monkeypatch,
        {
            "active": True,
            "project": "quotesense",
            "session_project": "portfolio",
            "expires_at": 1700,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        api_module.verify_flowpilot_gateway_session("gateway-bearer")

    assert exc_info.value.status_code == 403


def test_gateway_status_rejects_non_portfolio_origin_session(monkeypatch):
    _install_status_response(
        monkeypatch,
        {
            "active": True,
            "project": "flowpilot",
            "session_project": "quotesense",
            "expires_at": 1700,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        api_module.verify_flowpilot_gateway_session("gateway-bearer")

    assert exc_info.value.status_code == 403
