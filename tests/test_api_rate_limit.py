from fastapi.testclient import TestClient

import app.api.main as api_module
from app.core.rate_limit import MemoryRateLimiter


def test_memory_limiter_allows_limit_then_blocks_until_window_expires():
    now = [100.0]
    limiter = MemoryRateLimiter(clock=lambda: now[0])

    assert limiter.check("ip:test", limit=2, window_seconds=60) == (True, 0)
    assert limiter.check("ip:test", limit=2, window_seconds=60) == (True, 0)
    allowed, retry_after = limiter.check("ip:test", limit=2, window_seconds=60)
    assert allowed is False
    assert retry_after == 60

    now[0] += 60
    assert limiter.check("ip:test", limit=2, window_seconds=60) == (True, 0)


def test_mutating_api_requests_are_rate_limited_without_changing_route_contract(monkeypatch):
    from app.core import rate_limit

    monkeypatch.setattr(api_module.settings, "app_env", "development")
    monkeypatch.setattr(api_module.settings, "inline_execution", True)
    monkeypatch.setattr(api_module.settings, "api_write_ip_rate_limit_per_minute", 1)
    monkeypatch.setattr(api_module.settings, "api_write_principal_rate_limit_per_minute", 10)
    rate_limit.memory_limiter.clear()

    with TestClient(api_module.app) as client:
        first = client.post("/api/v1/runs")
        second = client.post("/api/v1/runs")

    assert first.status_code == 422
    assert second.status_code == 429
    assert second.headers["retry-after"]


def test_read_only_requests_are_not_covered_by_write_rate_limit(monkeypatch):
    from app.core import rate_limit

    monkeypatch.setattr(api_module.settings, "app_env", "development")
    monkeypatch.setattr(api_module.settings, "api_write_ip_rate_limit_per_minute", 1)
    rate_limit.memory_limiter.clear()

    with TestClient(api_module.app) as client:
        first = client.get("/health")
        second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
