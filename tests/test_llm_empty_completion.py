import httpx
import pytest

from app.core.config import Settings
from app.integrations.llm import LLMProvider


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
    responses = iter([_response(None), _response("{\"posts\": []}")])
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return next(responses)

    monkeypatch.setattr("app.integrations.llm.httpx.post", fake_post)
    monkeypatch.setattr("app.integrations.llm.time.sleep", lambda _seconds: None)

    result = provider.generate("system", "user")

    assert result["text"] == "{\"posts\": []}"
    assert len(calls) == 2


def test_empty_completion_fails_without_returning_null_text(monkeypatch, caplog):
    provider = _provider(max_retries=0)
    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        lambda *args, **kwargs: _response("  "),
    )

    with pytest.raises(RuntimeError, match="configured retries"):
        provider.generate("system", "user")

    assert "error_type=ValueError" in caplog.text
