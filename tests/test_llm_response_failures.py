import httpx
import pytest

from app.core.config import Settings
from app.integrations.llm import LLMProvider, LLMProviderError


def make_provider(max_retries: int = 2) -> LLMProvider:
    return LLMProvider(
        Settings(
            demo_mode=False,
            llm_provider="portfolio-gateway",
            openai_api_key="not-used-when-gateway-session-is-set",
            llm_gateway_url="https://gateway.example/v1",
            max_retries=max_retries,
            request_timeout_seconds=5,
        )
    )


def _with_gateway_session(token: str = "short-lived-session-token"):
    from app.core.llm_gateway_context import set_llm_gateway_token

    return set_llm_gateway_token(token)


def test_gateway_404_is_not_retried_and_reports_endpoint_failure(monkeypatch):
    from app.core.llm_gateway_context import reset_llm_gateway_token

    provider = make_provider()
    calls = []

    def missing_route(*args, **kwargs):
        calls.append(True)
        request = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
        return httpx.Response(404, request=request, json={"error": {"message": "not found"}})

    monkeypatch.setattr("app.integrations.llm.httpx.post", missing_route)
    monkeypatch.setattr(
        "app.integrations.llm.time.sleep",
        lambda _seconds: pytest.fail("404 is permanent and must not be retried"),
    )

    context = _with_gateway_session()
    try:
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate("system", "user")
    finally:
        reset_llm_gateway_token(context)

    assert exc_info.value.failure_code == "provider_endpoint_not_found"
    assert exc_info.value.status_code == 404
    assert "Gateway base URL includes /v1" in str(exc_info.value)
    assert len(calls) == 1


def test_gateway_503_remains_retryable(monkeypatch):
    from app.core.llm_gateway_context import reset_llm_gateway_token

    provider = make_provider(max_retries=1)
    calls = []
    sleeps = []

    def unavailable_then_success(*args, **kwargs):
        calls.append(True)
        request = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
        if len(calls) == 1:
            return httpx.Response(503, request=request, json={"detail": "temporarily unavailable"})
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "available"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setattr("app.integrations.llm.httpx.post", unavailable_then_success)
    monkeypatch.setattr("app.integrations.llm.time.sleep", sleeps.append)

    context = _with_gateway_session()
    try:
        result = provider.generate("system", "user")
    finally:
        reset_llm_gateway_token(context)

    assert result["text"] == "available"
    assert len(calls) == 2
    assert sleeps == [1]



def test_gateway_empty_completion_is_not_retried_and_has_specific_failure(monkeypatch):
    from app.core.llm_gateway_context import reset_llm_gateway_token

    provider = make_provider(max_retries=2)
    calls = []

    def empty_completion(*args, **kwargs):
        calls.append(True)
        request = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
        return httpx.Response(
            502,
            request=request,
            json={
                "error": {
                    "type": "empty_completion",
                    "code": "empty_completion",
                    "message": "The selected model exhausted its output-token budget.",
                }
            },
        )

    monkeypatch.setattr("app.integrations.llm.httpx.post", empty_completion)
    context = _with_gateway_session()
    try:
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate("system", "user")
    finally:
        reset_llm_gateway_token(context)

    assert exc_info.value.failure_code == "provider_empty_completion"
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 502
    assert len(calls) == 1
