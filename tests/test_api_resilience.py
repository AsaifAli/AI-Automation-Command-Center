from types import SimpleNamespace

import requests

from ui.api_resilience import resilient_get


def _response(status_code: int, retry_after: str = ""):
    return SimpleNamespace(
        status_code=status_code,
        headers={"Retry-After": retry_after} if retry_after else {},
    )


def test_resilient_get_retries_429_and_honors_bounded_retry_after():
    responses = iter([_response(429, "0"), _response(200)])
    calls = []
    waits = []

    def request():
        calls.append(True)
        return next(responses)

    result = resilient_get(request, sleep=waits.append)

    assert result.status_code == 200
    assert len(calls) == 2
    assert waits == [0.1]


def test_resilient_get_does_not_retry_permanent_404():
    calls = []

    def request():
        calls.append(True)
        return _response(404)

    result = resilient_get(request, sleep=lambda _seconds: None)

    assert result.status_code == 404
    assert len(calls) == 1


def test_resilient_get_retries_transient_network_error():
    calls = []
    waits = []

    def request():
        calls.append(True)
        if len(calls) == 1:
            raise requests.Timeout("temporary timeout")
        return _response(200)

    result = resilient_get(request, sleep=waits.append)

    assert result.status_code == 200
    assert len(calls) == 2
    assert waits == [0.5]
