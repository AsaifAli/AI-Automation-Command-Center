import httpx
import pytest

from app.core.config import Settings
from app.integrations.llm import LLMProvider


def _provider(max_retries: int = 2) -> LLMProvider:
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


def _response(content, *, usage=None):
    return httpx.Response(
        200,
        request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
        json={
            "model": "test-model",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage if usage is not None else {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
    )


def test_empty_completion_is_not_retried(monkeypatch, caplog):
    provider = _provider(max_retries=2)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return _response("  ")

    monkeypatch.setattr("app.integrations.llm.httpx.post", fake_post)
    monkeypatch.setattr(
        "app.integrations.llm.time.sleep",
        lambda _seconds: pytest.fail("A deterministic empty response must not retry"),
    )

    with pytest.raises(RuntimeError, match="empty text completion"):
        provider.generate("system", "user")

    assert len(calls) == 1
    assert "llm_response_invalid" in caplog.text
    assert "system" not in caplog.text
    assert "user" not in caplog.text


def test_malformed_json_response_is_not_retried(monkeypatch, caplog):
    provider = _provider(max_retries=2)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
            text="not-json",
            headers={"content-type": "text/plain"},
        )

    monkeypatch.setattr("app.integrations.llm.httpx.post", fake_post)
    monkeypatch.setattr(
        "app.integrations.llm.time.sleep",
        lambda _seconds: pytest.fail("A malformed success response must not retry"),
    )

    with pytest.raises(RuntimeError, match="unreadable response"):
        provider.generate("system", "user")

    assert len(calls) == 1
    assert "llm_response_invalid" in caplog.text


def test_invalid_usage_metadata_does_not_discard_valid_text(monkeypatch):
    provider = _provider(max_retries=0)
    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        lambda *args, **kwargs: _response(
            "The completion is valid.",
            usage={
                "prompt_tokens": "invalid",
                "completion_tokens": None,
                "total_tokens": {"bad": "value"},
            },
        ),
    )

    result = provider.generate("system", "user")

    assert result["text"] == "The completion is valid."
    assert result["usage"]["prompt_tokens"] == 0
    assert result["usage"]["completion_tokens"] == 0
    assert result["usage"]["total_tokens"] == 0


def test_typed_text_content_is_supported(monkeypatch):
    provider = _provider(max_retries=0)
    monkeypatch.setattr(
        "app.integrations.llm.httpx.post",
        lambda *args, **kwargs: _response([
            {"type": "text", "text": "First part."},
            {"type": "text", "text": "Second part."},
        ]),
    )

    result = provider.generate("system", "user")
    assert result["text"] == "First part.\nSecond part."
