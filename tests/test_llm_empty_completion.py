import httpx
import pytest

from app.core.config import Settings
from app.integrations.llm import LLMProvider, LLMProviderError


def _provider(max_retries: int) -> LLMProvider:
    return LLMProvider(
        Settings(
            demo_mode=False,
            llm_provider="test-provider",
            openai_api_key="unit-test-key",
            openai_base_url="https://provider.invalid/v1",
            max_retries=max_retries,
            request_timeout_seconds=1,
        )
    )


def _response(content):
    return httpx.Response(
        200,
        request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
        json={
            "model": "test-model",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        },
    )


def test_empty_completion_is_retried_then_usable_text_is_returned(monkeypatch):
    provider = _provider(max_retries=1)
    responses = iter([_response(None), _response('{"posts": []}')])
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return next(responses)

    monkeypatch.setattr("app.integrations.llm.httpx.post", fake_post)
    monkeypatch.setattr("app.integrations.llm.time.sleep", lambda _seconds: None)

    result = provider.generate("system", "user")

    assert result["text"] == '{"posts": []}'
    assert len(calls) == 2


def test_empty_completion_has_a_specific_safe_failure(monkeypatch, caplog):
    provider = _provider(max_retries=0)
    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        lambda *args, **kwargs: _response("  "),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate("system", "user")

    assert exc_info.value.failure_code == "provider_empty_completion"
    assert "HTTP 200 but no text completion" in str(exc_info.value)
    assert "failure_code=provider_empty_completion" in caplog.text


def test_not_found_endpoint_fails_without_retries(monkeypatch):
    provider = _provider(max_retries=2)
    response = httpx.Response(
        404,
        request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
        json={"detail": "not found"},
    )
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(True)
        return response

    monkeypatch.setattr("app.integrations.llm.httpx.post", fake_post)
    monkeypatch.setattr("app.integrations.llm.time.sleep", lambda _seconds: None)

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate("system", "user")

    assert exc_info.value.failure_code == "provider_endpoint_not_found"
    assert exc_info.value.status_code == 404
    assert len(calls) == 1


def test_provider_429_is_retried_once_when_configured(monkeypatch):
    provider = _provider(max_retries=1)
    limited = httpx.Response(
        429,
        request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
        headers={"Retry-After": "0"},
        json={"error": "rate limited"},
    )
    responses = iter([limited, _response("ready")])
    calls = []
    waits = []

    def fake_post(*args, **kwargs):
        calls.append(True)
        return next(responses)

    monkeypatch.setattr("app.integrations.llm.httpx.post", fake_post)
    monkeypatch.setattr("app.integrations.llm.time.sleep", waits.append)

    result = provider.generate("system", "user")

    assert result["text"] == "ready"
    assert len(calls) == 2
    assert waits == [0.25]


def test_openai_compatible_text_content_parts_are_supported(monkeypatch):
    provider = _provider(max_retries=0)
    response = _response([
        {"type": "text", "text": "First part"},
        {"type": "text", "text": "Second part"},
    ])
    monkeypatch.setattr("app.integrations.llm.httpx.post", lambda *args, **kwargs: response)

    result = provider.generate("system", "user")

    assert result["text"] == "First part\nSecond part"
