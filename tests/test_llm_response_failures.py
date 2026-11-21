import httpx
import pytest

from app.core.config import Settings
from app.integrations.llm import LLMProvider


def make_provider(max_retries=2):
    return LLMProvider(Settings(
        demo_mode=False,
        llm_provider="portfolio-gateway",
        openai_api_key="not-used-when-gateway-session-is-set",
        llm_gateway_url="https://gateway.example/v1",
        max_retries=max_retries,
        request_timeout_seconds=5,
    ))


def test_gateway_404_is_not_retried_and_explains_provider_model_mismatch(monkeypatch):
    from app.core.llm_gateway_context import reset_llm_gateway_token, set_llm_gateway_token

    provider = make_provider()
    calls = []

    def missing_model(*args, **kwargs):
        calls.append(1)
        request = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
        return httpx.Response(
            404,
            request=request,
            json={"error": {"message": "not found"}},
        )

    monkeypatch.setattr("app.integrations.llm.httpx.post", missing_model)
    monkeypatch.setattr(
        "app.integrations.llm.time.sleep",
        lambda _seconds: pytest.fail("404 is not retryable"),
    )

    context = set_llm_gateway_token("short-lived-session-token")
    try:
        with pytest.raises(RuntimeError, match="provider or model was not found"):
            provider.generate("system", "user")
    finally:
        reset_llm_gateway_token(context)

    assert len(calls) == 1


def test_gateway_503_remains_retryable(monkeypatch):
    provider = make_provider(max_retries=1)
    calls = []
    sleeps = []

    def unavailable_then_success(*args, **kwargs):
        calls.append(1)
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

    context = None
    from app.core.llm_gateway_context import reset_llm_gateway_token, set_llm_gateway_token
    context = set_llm_gateway_token("short-lived-session-token")
    try:
        result = provider.generate("system", "user")
    finally:
        reset_llm_gateway_token(context)

    assert result["text"] == "available"
    assert len(calls) == 2
    assert sleeps == [1]
