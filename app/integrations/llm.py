"""Vendor-neutral OpenAI-compatible LLM boundary with bounded retries and safe errors."""

from __future__ import annotations

import email.utils
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.llm_gateway_context import get_llm_gateway_token

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class LLMProviderError(RuntimeError):
    """Sanitized provider failure suitable for a workflow result and UI display."""

    def __init__(
        self,
        failure_code: str,
        public_message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(public_message)
        self.failure_code = failure_code
        self.public_message = public_message
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def _completion_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMProviderError(
            "provider_url_missing",
            "The model provider URL is not configured. Check the service provider URL or Gateway URL.",
        )
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _retry_after_seconds(headers: httpx.Headers, attempt: int) -> float:
    raw = headers.get("Retry-After", "").strip()
    delay: float | None = None
    if raw:
        try:
            delay = float(raw)
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                delay = (retry_at - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = None
    if delay is None:
        delay = min(1.0 * (2**attempt), 4.0)
    return min(max(delay, 0.25), 5.0)


def _safe_token_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    text_parts.append(part.strip())
                continue
            if not isinstance(part, dict):
                continue
            if part.get("type") not in {"text", "output_text", None}:
                continue
            value = part.get("text", part.get("content"))
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())
        return "\n".join(text_parts).strip()
    return ""


def _http_failure(response: httpx.Response) -> LLMProviderError:
    status = response.status_code
    if status in {401, 403}:
        return LLMProviderError(
            "provider_auth_failed",
            "The model provider rejected the configured credentials. Reopen the BYOK session or verify the service-level provider key.",
            status_code=status,
        )
    if status == 404:
        return LLMProviderError(
            "provider_endpoint_not_found",
            "The configured model endpoint returned 404. Verify that the Gateway base URL includes /v1 and that its chat-completions route is deployed.",
            status_code=status,
        )
    if status == 429:
        return LLMProviderError(
            "provider_rate_limited",
            "The model provider rate limit was reached. Wait briefly, then retry the workflow or choose a less busy model.",
            status_code=status,
            retryable=True,
            retry_after_seconds=_retry_after_seconds(response.headers, 0),
        )
    if status in RETRYABLE_HTTP_STATUSES:
        return LLMProviderError(
            "provider_temporarily_unavailable",
            f"The model provider is temporarily unavailable (HTTP {status}). Retry the workflow shortly.",
            status_code=status,
            retryable=True,
            retry_after_seconds=_retry_after_seconds(response.headers, 0),
        )
    if status in {400, 404, 413, 422}:
        return LLMProviderError(
            "provider_request_rejected",
            f"The model provider rejected the request (HTTP {status}). Verify the selected model and request configuration.",
            status_code=status,
        )
    return LLMProviderError(
        "provider_http_error",
        f"The model provider returned HTTP {status}. Verify the provider configuration and retry.",
        status_code=status,
        retryable=False,
    )


class LLMProvider:
    """Small provider boundary so agent logic stays vendor-neutral."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def gateway_session_active(self) -> bool:
        return bool(get_llm_gateway_token())

    def generate(self, system: str, user: str) -> dict[str, Any]:
        gateway_token = get_llm_gateway_token()
        gateway_url = (self.settings.llm_gateway_url or self.settings.llm_base_url).strip()

        # An explicit user Gateway session takes precedence over service demo mode.
        # Without either a session or a service key, live mode fails closed.
        if self.settings.demo_mode and not gateway_token:
            return {
                "text": self._demo(system, user),
                "provider": "demo",
                "model": "deterministic-demo",
                "usage": {
                    "provider": "demo",
                    "model": "deterministic-demo",
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "cost_estimate_available": False,
                },
            }

        if gateway_token and not gateway_url:
            raise LLMProviderError(
                "gateway_url_missing",
                "A BYOK session is active, but the FlowPilot Gateway URL is not configured.",
            )
        if not gateway_token and not self.settings.openai_api_key.strip():
            raise LLMProviderError(
                "provider_credentials_missing",
                "No model credentials are configured. Reopen the BYOK session or configure the service-level provider key.",
            )

        base_url = gateway_url if gateway_token else self.settings.openai_base_url
        url = _completion_url(base_url)
        payload = {
            "model": self.settings.llm_model or self.settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        api_key = gateway_token or self.settings.openai_api_key
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            configured_retries = int(self.settings.max_retries)
        except (TypeError, ValueError, OverflowError):
            configured_retries = 0
        max_retries = max(0, min(configured_retries, 2))

        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            failure: LLMProviderError | None = None
            retry_delay = min(1.0 * (2**attempt), 4.0)

            try:
                response = httpx.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.request_timeout_seconds,
                    follow_redirects=False,
                )
            except httpx.TimeoutException:
                failure = LLMProviderError(
                    "provider_timeout",
                    "The model provider did not respond before the request timed out. Retry the workflow or use a faster model.",
                    retryable=True,
                )
            except httpx.RequestError:
                failure = LLMProviderError(
                    "provider_connection_failed",
                    "FlowPilot could not connect to the model provider. Check the Gateway/provider service and retry.",
                    retryable=True,
                )
            else:
                if response.status_code >= 400:
                    failure = _http_failure(response)
                    if failure.retry_after_seconds is not None:
                        retry_delay = failure.retry_after_seconds
                else:
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                        failure = LLMProviderError(
                            "provider_invalid_json",
                            "The model endpoint returned HTTP 200 but its response was not valid JSON.",
                            retryable=True,
                        )

                    if failure is None:
                        if not isinstance(data, dict):
                            failure = LLMProviderError(
                                "provider_invalid_response",
                                "The model endpoint returned an unexpected response structure.",
                                retryable=True,
                            )
                        else:
                            choices = data.get("choices")
                            choice = (
                                choices[0]
                                if isinstance(choices, list) and choices and isinstance(choices[0], dict)
                                else None
                            )
                            message = choice.get("message") if isinstance(choice, dict) else None
                            if not isinstance(message, dict):
                                failure = LLMProviderError(
                                    "provider_missing_message",
                                    "The model endpoint returned HTTP 200 without a chat message. Verify the model and Gateway response format.",
                                    retryable=True,
                                )
                            else:
                                content = _message_text(message)
                                if not content:
                                    if message.get("refusal"):
                                        failure = LLMProviderError(
                                            "model_refusal",
                                            "The model declined to generate this output. Adjust the request and retry.",
                                        )
                                    elif message.get("tool_calls"):
                                        failure = LLMProviderError(
                                            "model_tool_call_unhandled",
                                            "The model returned a tool call, but tools are not enabled for this FlowPilot workflow.",
                                        )
                                    else:
                                        failure = LLMProviderError(
                                            "provider_empty_completion",
                                            "The model endpoint returned HTTP 200 but no text completion. Check the selected model and Gateway response format.",
                                            retryable=True,
                                        )
                                else:
                                    usage = data.get("usage")
                                    usage = usage if isinstance(usage, dict) else {}
                                    prompt_tokens = _safe_token_count(usage.get("prompt_tokens"))
                                    completion_tokens = _safe_token_count(usage.get("completion_tokens"))
                                    total_tokens = _safe_token_count(
                                        usage.get("total_tokens", prompt_tokens + completion_tokens)
                                    )
                                    cost = (
                                        prompt_tokens * self.settings.input_cost_per_million
                                        + completion_tokens * self.settings.output_cost_per_million
                                    ) / 1_000_000
                                    model = data.get("model") or self.settings.llm_model or self.settings.openai_model
                                    provider = "portfolio-gateway" if gateway_token else self.settings.llm_provider

                                    logger.info(
                                        "llm_request_complete provider=%s attempt=%s latency_ms=%s",
                                        provider,
                                        attempt + 1,
                                        int((time.perf_counter() - started) * 1000),
                                    )
                                    return {
                                        "text": content,
                                        "provider": provider,
                                        "model": model,
                                        "usage": {
                                            "provider": provider,
                                            "model": model,
                                            "prompt_tokens": prompt_tokens,
                                            "completion_tokens": completion_tokens,
                                            "total_tokens": total_tokens,
                                            "estimated_cost_usd": 0.0 if gateway_token else round(cost, 8),
                                            "cost_estimate_available": not bool(gateway_token),
                                        },
                                    }

            if failure is None:  # pragma: no cover - defensive guard
                failure = LLMProviderError(
                    "provider_unknown_error",
                    "The model provider request failed unexpectedly.",
                )

            logger.warning(
                "llm_request_failed provider=%s attempt=%s failure_code=%s status_code=%s retryable=%s",
                "portfolio-gateway" if gateway_token else self.settings.llm_provider,
                attempt + 1,
                failure.failure_code,
                failure.status_code,
                failure.retryable,
            )
            if not failure.retryable or attempt >= max_retries:
                raise failure
            time.sleep(min(max(retry_delay, 0.25), 5.0))

        raise LLMProviderError(
            "provider_exhausted_retries",
            "The model provider request failed after the configured retry attempts.",
        )  # pragma: no cover

    @staticmethod
    def _demo(system: str, user: str) -> str:
        return json.dumps(
            {
                "summary": "Deterministic demo output generated without external credentials.",
                "recommendation": (
                    "Review evidence, approve the proposed action, "
                    "then execute through an approved integration."
                ),
                "input_preview": user[:240],
                "guardrail": (
                    "External actions require human approval "
                    "in this portfolio demo."
                ),
            }
        )
