"""Redis-backed write quotas with a bounded local fallback for development.

Production uses Redis so limits are shared by API workers and instances. When
Redis is unavailable in production, the write request fails closed with 503.
Development and inline-demo modes use an in-process sliding-window limiter.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from redis import Redis
from redis.exceptions import RedisError


class RateLimitBackendUnavailable(RuntimeError):
    """Raised when the shared production rate-limit store cannot be reached."""


class MemoryRateLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        safe_limit = max(1, int(limit))
        safe_window = max(1, int(window_seconds))
        now = self._clock()
        cutoff = now - safe_window
        with self._lock:
            events = self._buckets[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= safe_limit:
                retry_after = max(1, int(events[0] + safe_window - now + 0.999))
                return False, retry_after
            events.append(now)
            if len(self._buckets) > 20_000:
                stale_keys = [
                    name
                    for name, bucket in self._buckets.items()
                    if not bucket or bucket[-1] <= cutoff
                ]
                for name in stale_keys[:5_000]:
                    self._buckets.pop(name, None)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


_RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if count == 1 or ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""

memory_limiter = MemoryRateLimiter()
_redis_clients: dict[str, Redis] = {}
_redis_clients_lock = threading.Lock()


def _redis_client(redis_url: str) -> Redis:
    with _redis_clients_lock:
        client = _redis_clients.get(redis_url)
        if client is None:
            client = Redis.from_url(
                redis_url,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                health_check_interval=30,
            )
            _redis_clients[redis_url] = client
        return client


def _bucket_result(
    client: Any,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    count, ttl = client.eval(
        _RATE_LIMIT_SCRIPT,
        1,
        key,
        max(1, int(window_seconds)),
    )
    retry_after = max(1, int(ttl or window_seconds))
    return int(count) <= max(1, int(limit)), retry_after


_UPSTASH_RATE_LIMIT_SCRIPT = """
local results = {}
for index = 1, #KEYS do
    local count = redis.call('INCR', KEYS[index])
    local ttl = redis.call('TTL', KEYS[index])
    if count == 1 or ttl < 0 then
        redis.call('EXPIRE', KEYS[index], ARGV[1])
        ttl = tonumber(ARGV[1])
    end
    table.insert(results, count)
    table.insert(results, ttl)
end
return results
"""
_upstash_client_instance: Any | None = None
_upstash_client_lock = threading.Lock()


def _upstash_client(settings: Any) -> Any:
    global _upstash_client_instance
    url = str(getattr(settings, "upstash_redis_rest_url", "") or "").strip()
    token = str(getattr(settings, "upstash_redis_rest_token", "") or "").strip()
    if not url or not token:
        raise RateLimitBackendUnavailable("Shared Upstash credentials are not configured.")
    with _upstash_client_lock:
        if _upstash_client_instance is None:
            try:
                from upstash_redis import Redis

                _upstash_client_instance = Redis(
                    url=url,
                    token=token,
                    rest_retries=0,
                    allow_telemetry=False,
                )
            except Exception as exc:
                raise RateLimitBackendUnavailable from exc
    return _upstash_client_instance


def check_request_limits(
    *,
    client_ip: str,
    api_key: str,
    settings: Any,
) -> tuple[bool, int]:
    """Check an IP quota and, when present, a credential-fingerprint quota."""
    window = max(1, int(settings.api_rate_limit_window_seconds))
    ip_digest = hashlib.sha256((client_ip or "unknown").encode("utf-8")).hexdigest()
    buckets = [
        (
            f"flowpilot:ratelimit:v1:ip:{ip_digest}",
            settings.api_write_ip_rate_limit_per_minute,
        )
    ]
    if api_key:
        key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        buckets.append(
            (
                f"flowpilot:ratelimit:v1:credential:{key_digest}",
                settings.api_write_principal_rate_limit_per_minute,
            )
        )

    is_production = str(settings.app_env).strip().lower() in {"prod", "production"}
    upstash_url = str(getattr(settings, "upstash_redis_rest_url", "") or "").strip()
    upstash_token = str(getattr(settings, "upstash_redis_rest_token", "") or "").strip()

    # Prefer the existing Upstash REST database in both inline and queued
    # production modes. One Lua EVAL checks IP and principal counters atomically
    # in a single HTTP request; app-specific prefixes avoid Gateway key overlap.
    if upstash_url or upstash_token or is_production and settings.inline_execution:
        try:
            client = _upstash_client(settings)
            keys = [key for key, _limit in buckets]
            args = [window, *[max(1, int(limit)) for _key, limit in buckets]]
            result = client.eval(
                _UPSTASH_RATE_LIMIT_SCRIPT,
                keys=keys,
                args=args,
            )
            if not isinstance(result, (list, tuple)) or len(result) != len(buckets) * 2:
                raise RateLimitBackendUnavailable("Upstash returned an invalid quota response.")
            for index, (_key, limit) in enumerate(buckets):
                count = int(result[index * 2])
                retry_after = max(1, int(result[index * 2 + 1] or window))
                if count > max(1, int(limit)):
                    return False, retry_after
            return True, 0
        except RateLimitBackendUnavailable:
            raise
        except Exception as exc:
            raise RateLimitBackendUnavailable from exc

    # Backward-compatible Redis TCP path for the optional queued-worker
    # topology. It is not used by the current inline free-tier deployment.
    if is_production and not settings.inline_execution:
        try:
            client = _redis_client(settings.redis_url)
            for key, limit in buckets:
                allowed, retry_after = _bucket_result(
                    client,
                    key,
                    limit=limit,
                    window_seconds=window,
                )
                if not allowed:
                    return False, retry_after
            return True, 0
        except RedisError as exc:
            raise RateLimitBackendUnavailable from exc

    if is_production:
        raise RateLimitBackendUnavailable("Shared production rate limiting is not configured.")

    for key, limit in buckets:
        allowed, retry_after = memory_limiter.check(
            key,
            limit=limit,
            window_seconds=window,
        )
        if not allowed:
            return False, retry_after
    return True, 0
