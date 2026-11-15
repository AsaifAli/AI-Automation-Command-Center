from ui.api_access import build_api_headers


def test_api_key_is_attached_when_configured():
    assert build_api_headers("operator-key") == {"X-API-Key": "operator-key"}


def test_gateway_token_requires_a_future_expiry():
    headers = build_api_headers(
        "operator-key",
        "gateway-token",
        gateway_expires_at=120,
        now=100,
    )
    assert headers == {
        "X-API-Key": "operator-key",
        "X-LLM-Gateway-Token": "gateway-token",
    }


def test_expired_gateway_token_is_not_forwarded():
    headers = build_api_headers(
        "operator-key",
        "gateway-token",
        gateway_expires_at=99,
        now=100,
    )
    assert headers == {"X-API-Key": "operator-key"}


def test_malformed_expiry_does_not_forward_gateway_token():
    headers = build_api_headers(
        "operator-key",
        "gateway-token",
        gateway_expires_at="not-a-timestamp",
        now=100,
    )
    assert headers == {"X-API-Key": "operator-key"}
