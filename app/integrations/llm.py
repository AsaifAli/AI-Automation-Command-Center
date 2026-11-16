
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.llm_gateway_context import get_llm_gateway_token

logger = logging.getLogger(__name__)


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

        for attempt in range(self.settings.max_retries + 1):
            started = time.perf_counter()

            try:
                response = httpx.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.request_timeout_seconds,
                )
                response.raise_for_status()

                data = response.json()
                choices = data.get("choices")
                message = (
                    choices[0].get("message")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict)
                    else None
                )
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Provider returned an empty text completion")
                usage = data.get("usage", {})
                prompt_tokens = int(usage.get("prompt_tokens", 0))
                completion_tokens = int(usage.get("completion_tokens", 0))
                total_tokens = int(
                    usage.get(
                        "total_tokens",
                        prompt_tokens + completion_tokens,
                    )
                )

                cost = (
                    prompt_tokens * self.settings.input_cost_per_million
                    + completion_tokens * self.settings.output_cost_per_million
                ) / 1_000_000

                logger.info(
                    "llm_request_complete attempt=%s latency_ms=%s",
                    attempt + 1,
                    int((time.perf_counter() - started) * 1000),
                )

                return {
                    "text": content.strip(),
                    "provider": "portfolio-gateway" if gateway_token else self.settings.llm_provider,
                    "model": data.get(
                        "model",
                        self.settings.llm_model or self.settings.openai_model,
                    ),
                    "usage": {
                        "provider": "portfolio-gateway" if gateway_token else self.settings.llm_provider,
                        "model": data.get(
                            "model",
                            self.settings.llm_model or self.settings.openai_model,
                        ),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        # The shared Gateway does not return provider-specific
                        # pricing. Never show a LiteLLM service-rate estimate for BYOK.
                        "estimated_cost_usd": 0.0 if gateway_token else round(cost, 8),
                        "cost_estimate_available": not bool(gateway_token),
                    },
                }

            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                status_code = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )

                logger.warning(
                    "llm_request_failed provider=%s attempt=%s error_type=%s status_code=%s",
                    self.settings.llm_provider,
                    attempt + 1,
                    type(exc).__name__,
                    status_code,
                )

                if attempt < self.settings.max_retries:
                    time.sleep(min(2**attempt, 4))

        raise RuntimeError(
            "LLM provider request failed after configured retries."
        ) from None

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
