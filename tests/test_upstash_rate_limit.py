import pytest

from app.core import rate_limit


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.calls = []

    def eval(self, script, *, keys, args):
        self.calls.append((script, list(keys), list(args)))
        results = []
        for index, key in enumerate(keys):
            count = self.counts.get(key, 0) + 1
            self.counts[key] = count
            results.extend([count, args[0]])
        return results


def test_inline_production_uses_shared_upstash_for_ip_and_api_key(monkeypatch):
    from app.core.config import Settings

    client = FakeRedis()
    monkeypatch.setattr(rate_limit, "_upstash_client", lambda _settings: client)
    settings = Settings(
        app_env="production",
        api_auth_enabled=True,
        api_auth_require_keys=True,
        api_admin_keys="configured-admin-key",
        inline_execution=True,
        upstash_redis_rest_url="https://redis.example",
        upstash_redis_rest_token="test-token",
        api_write_ip_rate_limit_per_minute=10,
        api_write_principal_rate_limit_per_minute=1,
        api_rate_limit_window_seconds=60,
    )

    first = rate_limit.check_request_limits(
        client_ip="203.0.113.10",
        api_key="operator-secret",
        settings=settings,
    )
    second = rate_limit.check_request_limits(
        client_ip="203.0.113.10",
        api_key="operator-secret",
        settings=settings,
    )

    assert first == (True, 0)
    assert second[0] is False
    assert len(client.calls) == 2
    script, keys, args = client.calls[0]
    assert "INCR" in script and "EXPIRE" in script
    assert len(keys) == 2
    assert all(key.startswith("flowpilot:ratelimit:v1:") for key in keys)
    assert all("operator-secret" not in key for key in keys)
    assert args == [60, 10, 1]


def test_inline_production_fails_closed_when_upstash_is_unavailable(monkeypatch):
    from app.core.config import Settings

    def unavailable(_settings):
        raise rate_limit.RateLimitBackendUnavailable

    monkeypatch.setattr(rate_limit, "_upstash_client", unavailable)
    settings = Settings(
        app_env="production",
        api_auth_enabled=True,
        api_auth_require_keys=True,
        api_admin_keys="configured-admin-key",
        inline_execution=True,
        upstash_redis_rest_url="https://redis.example",
        upstash_redis_rest_token="test-token",
    )

    with pytest.raises(rate_limit.RateLimitBackendUnavailable):
        rate_limit.check_request_limits(
            client_ip="203.0.113.11",
            api_key="operator-secret",
            settings=settings,
        )
