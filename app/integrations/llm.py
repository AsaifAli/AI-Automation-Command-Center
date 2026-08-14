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

    def generate(self, system: str, user: str) -> dict[str, Any]:
        if self.settings.demo_mode or not self.settings.openai_api_key:
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
                },
            }

        gateway_token = get_llm_gateway_token()
        gateway_url = (self.settings.llm_gateway_url or self.settings.llm_base_url).strip()
        if gateway_token and gateway_url:
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
        last_error: Exception | None = None

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
                usage = data.get("usage", {})
                prompt_tokens = int(usage.get("prompt_tokens", 0))
                completion_tokens = int(usage.get("completion_tokens", 0))
                total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))
                cost = (
                    prompt_tokens * self.settings.input_cost_per_million
                    + completion_tokens * self.settings.output_cost_per_million
                ) / 1_000_000
                logger.info("llm_request_complete attempt=%s latency_ms=%s", attempt + 1, int((time.perf_counter() - started) * 1000))
                return {
                    "text": data["choices"][0]["message"]["content"],
                    "provider": self.settings.llm_provider,
                    "model": data.get("model", self.settings.openai_model),
                    "usage": {
                        "provider": self.settings.llm_provider,
                        "model": data.get("model", self.settings.openai_model),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "estimated_cost_usd": round(cost, 8),
                    },
                }
            except Exception as exc:  # provider boundary: translate third-party errors into one retryable path
                last_error = exc
                logger.warning("llm_request_failed attempt=%s error=%s", attempt + 1, exc)
                if attempt < self.settings.max_retries:
                    time.sleep(min(2**attempt, 4))

        raise RuntimeError(f"LLM provider failed after retries: {last_error}")

    @staticmethod
    def _demo(system: str, user: str) -> str:
        return json.dumps(
            {
                "summary": "Deterministic demo output generated without external credentials.",
                "recommendation": "Review evidence, approve the proposed action, then execute through an approved integration.",
                "input_preview": user[:240],
                "guardrail": "External actions require human approval in this portfolio demo.",
            }
        )
