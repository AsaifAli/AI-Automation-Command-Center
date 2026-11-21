import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.llm_gateway_context import get_llm_gateway_token

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 500, 502, 503, 504, 529})


class LLMProvider:
    """Small provider boundary so agent logic stays vendor-neutral."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def gateway_session_active(self) -> bool:
        return bool(get_llm_gateway_token())

    @staticmethod
    def _safe_token_count(value: Any) -> int:
        """Parse usage counters without turning malformed metadata into a failed run."""
        if isinstance(value, bool):
            return 0
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, parsed)

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

        # Some OpenAI-compatible models return content as typed text blocks.
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "\n".join(part for part in parts if part.strip()).strip()

        refusal = message.get("refusal")
        if isinstance(refusal, str):
            return refusal.strip()
        return ""

    @staticmethod
    def _retry_after(response: httpx.Response) -> str:
        value = str(response.headers.get("Retry-After", "")).strip()
        if value.isdigit() and len(value) <= 6:
            return f" Retry after approximately {value} seconds."
        return " Wait briefly and retry."

    @staticmethod
    def _status_error_message(status_code: int, response: httpx.Response) -> str:
        if status_code == 401 or status_code == 403:
            return (
                f"The provider rejected the active API credential (HTTP {status_code}). "
                "Reconnect BYOK with a valid provider key and check its permissions."
            )
        if status_code == 402:
            return (
                "The provider account has insufficient credits or billing capacity (HTTP 402). "
                "Check the provider account before retrying."
            )
        if status_code == 404:
            return (
                "The selected provider or model was not found by the Gateway (HTTP 404). "
                "Verify the provider and model ID, then reconnect the BYOK session."
            )
        if status_code == 429:
            return (
                "The selected provider rate-limited this request (HTTP 429)."
                + LLMProvider._retry_after(response)
            )
        if status_code in {400, 413, 422}:
            return (
                f"The provider rejected the model or request parameters (HTTP {status_code}). "
                "Check the selected model ID and request settings."
            )
        if status_code >= 500:
            return (
                f"The selected provider or Gateway is temporarily unavailable (HTTP {status_code}). "
                "Please retry after a short wait."
            )
        return f"The LLM provider request was rejected (HTTP {status_code})."

    @staticmethod
    def _log_invalid_response(
        response: httpx.Response,
        data: Any,
        *,
        choice_count: int = 0,
        finish_reason: str | None = None,
        has_content: bool = False,
        tool_call_count: int = 0,
    ) -> None:
        """Record only response shape metadata; never log provider text or credentials."""
        keys = sorted(str(key) for key in data)[:20] if isinstance(data, dict) else []
        logger.warning(
            "llm_response_invalid status_code=%s content_type=%s response_bytes=%s "
            "top_level_keys=%s choice_count=%s finish_reason=%s has_content=%s tool_call_count=%s",
            response.status_code,
            response.headers.get("content-type", "unknown")[:80],
            len(response.content or b""),
            keys,
            choice_count,
            str(finish_reason or "unknown")[:40],
            has_content,
            tool_call_count,
        )

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
            raise RuntimeError(
                "An LLM Gateway session is present, but the Gateway base URL is not configured."
            )
        if not gateway_token and not self.settings.openai_api_key.strip():
            raise RuntimeError(
                "No LLM credentials are configured. Set a service-level provider key or use an active Gateway session."
            )

        if gateway_token:
            url = gateway_url.rstrip("/") + "/chat/completions"
        else:
            url = self.settings.openai_base_url.rstrip("/") + "/chat/completions"

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
        max_retries = max(0, min(int(self.settings.max_retries), 3))

        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.request_timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                failed_response = exc.response
                status_code = failed_response.status_code
                retryable = status_code in RETRYABLE_HTTP_STATUS_CODES
                logger.warning(
                    "llm_request_failed provider=%s attempt=%s error_type=HTTPStatusError "
                    "status_code=%s retryable=%s retry_after=%s",
                    self.settings.llm_provider,
                    attempt + 1,
                    status_code,
                    retryable,
                    self._retry_after(failed_response).strip()[:60] if status_code == 429 else "",
                )

                if retryable and attempt < max_retries:
                    time.sleep(min(2**attempt, 4))
                    continue

                if retryable:
                    raise RuntimeError(
                        "LLM provider request failed after configured retries."
                    ) from None
                raise RuntimeError(
                    self._status_error_message(status_code, failed_response)
                ) from None
            except httpx.TransportError as exc:
                logger.warning(
                    "llm_request_failed provider=%s attempt=%s error_type=%s "
                    "status_code=None retryable=True",
                    self.settings.llm_provider,
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < max_retries:
                    time.sleep(min(2**attempt, 4))
                    continue
                raise RuntimeError(
                    "LLM provider request failed after configured retries."
                ) from None
            except httpx.HTTPError as exc:
                logger.warning(
                    "llm_request_failed provider=%s attempt=%s error_type=%s "
                    "status_code=None retryable=False",
                    self.settings.llm_provider,
                    attempt + 1,
                    type(exc).__name__,
                )
                raise RuntimeError(
                    "The LLM request could not be prepared or sent. Check the Gateway URL and request configuration."
                ) from None

            try:
                data = response.json()
            except ValueError:
                self._log_invalid_response(response, None)
                raise RuntimeError(
                    "The Gateway returned an unreadable response instead of valid JSON. Please retry or reconnect BYOK."
                ) from None

            if not isinstance(data, dict):
                self._log_invalid_response(response, data)
                raise TypeError(
                    "The Gateway returned an unexpected response format. Please retry or reconnect BYOK."
                ) from None

            choices = data.get("choices")
            choice_count = len(choices) if isinstance(choices, list) else 0
            first_choice = choices[0] if isinstance(choices, list) and choices else None
            message = (
                first_choice.get("message")
                if isinstance(first_choice, dict)
                else None
            )
            message = message if isinstance(message, dict) else {}
            finish_reason = (
                first_choice.get("finish_reason")
                if isinstance(first_choice, dict)
                else None
            )
            tool_calls = message.get("tool_calls")
            tool_call_count = len(tool_calls) if isinstance(tool_calls, list) else 0
            content = self._extract_text(message)

            if not content:
                self._log_invalid_response(
                    response,
                    data,
                    choice_count=choice_count,
                    finish_reason=finish_reason,
                    has_content=False,
                    tool_call_count=tool_call_count,
                )
                raise RuntimeError(
                    "The Gateway returned an empty text completion. The selected model may not support this text workflow; verify the model and reconnect BYOK."
                ) from None

            usage_value = data.get("usage")
            usage = usage_value if isinstance(usage_value, dict) else {}
            prompt_tokens = self._safe_token_count(usage.get("prompt_tokens"))
            completion_tokens = self._safe_token_count(usage.get("completion_tokens"))
            total_tokens = self._safe_token_count(
                usage.get("total_tokens", prompt_tokens + completion_tokens)
            )
            if total_tokens == 0 and (prompt_tokens or completion_tokens):
                total_tokens = prompt_tokens + completion_tokens

            cost = (
                prompt_tokens * self.settings.input_cost_per_million
                + completion_tokens * self.settings.output_cost_per_million
            ) / 1_000_000

            model_value = data.get("model")
            model_name = (
                model_value.strip()
                if isinstance(model_value, str) and model_value.strip()
                else self.settings.llm_model or self.settings.openai_model
            )
            provider_name = (
                "portfolio-gateway" if gateway_token else self.settings.llm_provider
            )

            logger.info(
                "llm_request_complete attempt=%s latency_ms=%s response_choices=%s "
                "finish_reason=%s usage_present=%s",
                attempt + 1,
                int((time.perf_counter() - started) * 1000),
                choice_count,
                str(finish_reason or "unknown")[:40],
                bool(usage),
            )

            return {
                "text": content,
                "provider": provider_name,
                "model": model_name,
                "usage": {
                    "provider": provider_name,
                    "model": model_name,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    # The shared Gateway does not return provider-specific
                    # pricing. Never show a LiteLLM service-rate estimate for BYOK.
                    "estimated_cost_usd": 0.0 if gateway_token else round(cost, 8),
                    "cost_estimate_available": not bool(gateway_token),
                },
            }

        raise RuntimeError("LLM provider request failed after configured retries.") from None

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
