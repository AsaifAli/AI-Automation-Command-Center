import json

import pytest

from app.agents.content import CHANNEL_LIMITS, CHANNEL_TARGETS, ContentAgent, ContentGenerationError
from app.core.config import Settings


class FakeLiveLLM:
    def __init__(self, text):
        self.responses = list(text) if isinstance(text, list) else [text]
        self.calls = []
        self.settings = Settings(
            app_env="development",
            api_auth_enabled=False,
            demo_mode=False,
        )
        self.gateway_active = False

    @property
    def gateway_session_active(self):
        return self.gateway_active

    def generate(self, system, user):
        self.calls.append((system, user))
        text = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        call_number = len(self.calls)
        return {
            "text": text,
            "provider": "fake-provider",
            "model": "fake-model",
            "usage": {
                "provider": "fake-provider",
                "model": "fake-model",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "estimated_cost_usd": 0.001,
                "cost_estimate_available": True,
                "call_number": call_number,
            },
        }


def _response(topic, channel, draft, strategy_note="Test strategy"):
    return json.dumps({
        "posts": [{"topic": topic, "channel": channel, "draft": draft}],
        "strategy_note": strategy_note,
    })


def test_live_content_drafts_come_from_model_response():
    topic = "AI agents in finance, operations and reconciliation"
    generated = {
        "posts": [
            {
                "topic": topic,
                "channel": "linkedin",
                "draft": (
                    "Finance teams can use AI agents to reconcile exceptions faster "
                    "while preserving human control."
                ),
            }
        ],
        "strategy_note": "Focus on measurable operational outcomes.",
    }
    llm = FakeLiveLLM(json.dumps(generated))

    result = ContentAgent(llm).run(
        {
            "topics": [topic],
            "channels": ["linkedin"],
            "tone": "insightful",
        }
    )

    assert result["count"] == 1
    assert result["posts"][0]["draft"] == generated["posts"][0]["draft"]
    assert result["posts"][0]["review_status"] == "needs_review"
    assert result["posts"][0]["publish_action"] == "human_approval_required"
    assert result["strategy_note"] == generated["strategy_note"]
    assert len(llm.calls) == 1
    assert topic in llm.calls[0][1]
    prompt_payload = json.loads(llm.calls[0][1])
    assert prompt_payload["hard_character_limits"] == CHANNEL_LIMITS
    assert prompt_payload["preferred_character_targets"] == CHANNEL_TARGETS


def test_live_content_requires_every_requested_topic_channel_pair():
    llm = FakeLiveLLM(json.dumps({"posts": [], "strategy_note": "No posts"}))

    with pytest.raises(ContentGenerationError, match="omitted 1"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["linkedin"]})

    assert len(llm.calls) == 1


def test_live_content_rejects_invalid_json_instead_of_fabricating_drafts():
    llm = FakeLiveLLM("This is not JSON")

    with pytest.raises(ContentGenerationError, match="valid structured output"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["linkedin"]})

    assert len(llm.calls) == 1


def test_active_gateway_session_uses_live_content_even_when_demo_mode_is_on():
    generated = {
        "posts": [
            {
                "topic": "Finance reconciliation with AI agents",
                "channel": "linkedin",
                "draft": (
                    "AI agents can classify reconciliation exceptions, gather evidence, "
                    "and route decisions to accountable reviewers."
                ),
            }
        ],
        "strategy_note": "Lead with the operational workflow and human controls.",
    }
    llm = FakeLiveLLM(json.dumps(generated))
    llm.settings.demo_mode = True
    llm.gateway_active = True

    result = ContentAgent(llm).run(
        {
            "topics": ["Finance reconciliation with AI agents"],
            "channels": ["linkedin"],
        }
    )

    assert result["posts"][0]["draft"] == generated["posts"][0]["draft"]
    assert "Demo draft" not in result["posts"][0]["draft"]


def test_live_content_repairs_over_limit_draft_once_and_aggregates_usage():
    topic = "AI"
    llm = FakeLiveLLM([
        _response(
            topic,
            "telegram",
            "t" * (CHANNEL_LIMITS["telegram"] + 1),
            "Overlong draft",
        ),
        _response(topic, "telegram", "A concise Telegram draft about practical AI operations."),
    ])

    result = ContentAgent(llm).run({"topics": [topic], "channels": ["telegram"]})

    assert len(llm.calls) == 2
    assert result["posts"][0]["draft"] == "A concise Telegram draft about practical AI operations."
    assert result["posts"][0]["review_status"] == "needs_review"
    assert result["posts"][0]["publish_action"] == "human_approval_required"
    repair_payload = json.loads(llm.calls[1][1])
    assert repair_payload["correction"]["violations"] == [{
        "topic": topic,
        "channel": "telegram",
        "actual_characters": CHANNEL_LIMITS["telegram"] + 1,
        "character_limit": CHANNEL_LIMITS["telegram"],
        "preferred_target": CHANNEL_TARGETS["telegram"],
    }]
    assert result["_usage"]["prompt_tokens"] == 20
    assert result["_usage"]["completion_tokens"] == 10
    assert result["_usage"]["total_tokens"] == 30
    assert result["_usage"]["estimated_cost_usd"] == 0.002


def test_live_content_fails_closed_if_repair_still_exceeds_channel_limit():
    over_limit = _response("AI", "x", "x" * (CHANNEL_LIMITS["x"] + 1))
    llm = FakeLiveLLM([over_limit, over_limit])

    with pytest.raises(ContentGenerationError, match="exceeds its 280-character limit"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["x"]})

    assert len(llm.calls) == 2
