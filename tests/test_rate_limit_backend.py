import pytest
from redis.exceptions import RedisError

from app.core import rate_limit
from app.core.config import Settings


class FakeRedis:
    def __init__(self):
        self.counts = {}

    def eval(self, script, key_count, key, window_seconds):
        assert key_count == 1
        assert "INCR" in script
        assert "EXPIRE" in script
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key], window_seconds


def test_production_rate_limits_use_shared_redis_keys(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis_client", lambda _url: client)
    settings = Settings(
        app_env="production",
        api_auth_enabled=True,
        api_admin_keys="test-admin-key",
        inline_execution=False,
        redis_url="redis://redis.example:6379/0",
        api_write_ip_rate_limit_per_minute=5,
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
    assert len(client.counts) == 2
    assert all("operator-secret" not in key for key in client.counts)


def test_production_rate_limit_fails_closed_when_redis_is_unavailable(monkeypatch):
    def unavailable(_url):
        raise RedisError("offline")

    monkeypatch.setattr(rate_limit, "_redis_client", unavailable)
    settings = Settings(
        app_env="production",
        inline_execution=False,
        redis_url="redis://redis.example:6379/0",
    )

    with pytest.raises(rate_limit.RateLimitBackendUnavailable):
        rate_limit.check_request_limits(
            client_ip="203.0.113.11",
            api_key="operator-secret",
            settings=settings,
        )
