import pytest
import requests

from ui.api_startup import APIStartupError, load_api_startup_data


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self.payload


def decode(response, context):
    if not response.ok:
        raise RuntimeError(f"{context} failed ({response.status_code}): unavailable")
    return response.json()


def test_api_startup_retries_connection_errors_then_returns_payload():
    calls = []
    sleeps = iter([4])

    def api_get(path):
        calls.append(path)
        if len(calls) <= 1:
            raise requests.ConnectionError("private host detail")
        return FakeResponse(payload={"path": path})

    health, metrics = load_api_startup_data(
        api_get,
        decode,
        startup_timeout_seconds=20,
        retry_interval_seconds=4,
        sleep=lambda _seconds: next(sleeps),
        monotonic=iter([0, 0, 0, 0, 4, 4, 4]).__next__,
    )

    assert health == {"path": "/health"}
    assert metrics == {"path": "/metrics"}
    assert calls == ["/health", "/health", "/metrics"]


def test_api_startup_retries_transient_render_statuses():
    calls = []

    def api_get(path):
        calls.append(path)
        if len(calls) == 1:
            return FakeResponse(503)
        return FakeResponse(payload={"path": path})

    health, metrics = load_api_startup_data(
        api_get,
        decode,
        startup_timeout_seconds=10,
        retry_interval_seconds=1,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0,
    )

    assert health == {"path": "/health"}
    assert metrics == {"path": "/metrics"}
    assert calls == ["/health", "/health", "/metrics"]


def test_api_startup_does_not_retry_auth_or_route_errors():
    calls = []

    def api_get(path):
        calls.append(path)
        return FakeResponse(401, {"detail": "Access denied"})

    with pytest.raises(APIStartupError, match="HTTP 401"):
        load_api_startup_data(
            api_get,
            decode,
            startup_timeout_seconds=30,
            retry_interval_seconds=1,
            sleep=lambda _seconds: pytest.fail("Auth failures must not retry"),
            monotonic=lambda: 0,
        )

    assert calls == ["/health"]


def test_api_startup_stops_after_bounded_deadline():
    calls = []

    def api_get(path):
        calls.append(path)
        raise requests.ConnectionError("not available")

    ticks = iter([0, 0, 4, 8])
    with pytest.raises(APIStartupError, match="within 8 seconds"):
        load_api_startup_data(
            api_get,
            decode,
            startup_timeout_seconds=8,
            retry_interval_seconds=4,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(ticks),
        )

    assert calls == ["/health", "/health", "/health"]
