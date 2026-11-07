
import httpx
import pytest

from app.core.config import Settings
from app.integrations.llm import LLMProvider


def make_provider(max_retries: int = 1) -> LLMProvider:
    settings = Settings(
        demo_mode=False,
        llm_provider="test-provider",
        openai_api_key="unit-test-api-key",
        openai_base_url="https://provider.invalid/v1",
        max_retries=max_retries,
        request_timeout_seconds=1,
    )
    return LLMProvider(settings)


def test_provider_exception_details_are_not_logged_or_returned(
    monkeypatch,
    caplog,
):
    provider = make_provider(max_retries=0)

    sensitive_url = (
        "https://provider.invalid/v1/chat/completions"
        "?token=do-not-log"
    )

    def failing_post(*args, **kwargs):
        request = httpx.Request("POST", sensitive_url)
        raise httpx.ConnectError(
            "private upstream detail",
            request=request,
        )

    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        failing_post,
    )

    with pytest.raises(RuntimeError) as exc_info:
        provider.generate("system", "user")

    assert str(exc_info.value) == (
        "LLM provider request failed after configured retries."
    )
    assert "private upstream detail" not in str(exc_info.value)
    assert "do-not-log" not in str(exc_info.value)
    assert "private upstream detail" not in caplog.text
    assert "do-not-log" not in caplog.text
    assert "error_type=ConnectError" in caplog.text


def test_provider_retry_count_and_backoff_are_preserved(monkeypatch):
    provider = make_provider(max_retries=2)

    calls = []
    sleeps = []

    def failing_post(*args, **kwargs):
        calls.append(1)
        request = httpx.Request(
            "POST",
            "https://provider.invalid/v1/chat/completions",
        )
        raise httpx.ConnectError(
            "upstream unavailable",
            request=request,
        )

    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        failing_post,
    )
    monkeypatch.setattr(
        "app.integrations.llm.time.sleep",
        sleeps.append,
    )

    with pytest.raises(RuntimeError, match="configured retries"):
        provider.generate("system", "user")

    assert len(calls) == 3
    assert sleeps == [1, 2]


def test_http_status_is_logged_without_upstream_response_details(
    monkeypatch,
    caplog,
):
    provider = make_provider(max_retries=0)

    def failing_post(*args, **kwargs):
        request = httpx.Request(
            "POST",
            "https://provider.invalid/v1/chat/completions"
            "?token=private-token",
        )
        return httpx.Response(
            503,
            request=request,
            text="private upstream response",
        )

    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        failing_post,
    )

    with pytest.raises(RuntimeError, match="configured retries"):
        provider.generate("system", "user")

    assert "status_code=503" in caplog.text
    assert "private-token" not in caplog.text
    assert "private upstream response" not in caplog.text
