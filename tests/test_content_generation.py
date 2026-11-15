import json

import pytest

from app.agents.content import ContentAgent, ContentGenerationError
from app.core.config import Settings


class FakeLiveLLM:
    def __init__(self, text):
        self.text = text
        self.settings = Settings(
            app_env="development",
            api_auth_enabled=False,
            demo_mode=False,
        )
        self.calls = []
        self.gateway_active = False

    @property
    def gateway_session_active(self):
        return self.gateway_active

    def generate(self, system, user):
        self.calls.append((system, user))
        return {
            "text": self.text,
            "provider": "fake-provider",
            "model": "fake-model",
            "usage": {"provider": "fake-provider", "model": "fake-model"},
        }


def test_live_content_drafts_come_from_model_response():
    generated = {
        "posts": [
            {
                "topic": "AI agents in finance, operations and reconciliation",
                "channel": "linkedin",
                "draft": "Finance teams can use AI agents to reconcile exceptions faster while preserving human control.",
            }
        ],
        "strategy_note": "Focus on measurable operational outcomes.",
    }
    llm = FakeLiveLLM(json.dumps(generated))

    result = ContentAgent(llm).run(
        {
            "topics": ["AI agents in finance, operations and reconciliation"],
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
    assert "AI agents in finance, operations and reconciliation" in llm.calls[0][1]


def test_live_content_requires_every_requested_topic_channel_pair():
    llm = FakeLiveLLM(json.dumps({"posts": [], "strategy_note": "No posts"}))

    with pytest.raises(ContentGenerationError, match="omitted 1"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["linkedin"]})


def test_live_content_rejects_invalid_json_instead_of_fabricating_drafts():
    llm = FakeLiveLLM("This is not JSON")

    with pytest.raises(ContentGenerationError, match="valid structured output"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["linkedin"]})


def test_active_gateway_session_uses_live_content_even_when_demo_mode_is_on():
    generated = {
        "posts": [
            {
                "topic": "Finance reconciliation with AI agents",
                "channel": "linkedin",
                "draft": "AI agents can classify reconciliation exceptions, gather evidence, and route decisions to accountable reviewers.",
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


def test_live_content_rejects_drafts_that_exceed_channel_limit():
    generated = {
        "posts": [
            {
                "topic": "AI",
                "channel": "x",
                "draft": "x" * 281,
            }
        ],
        "strategy_note": "Shorten the post.",
    }
    llm = FakeLiveLLM(json.dumps(generated))

    with pytest.raises(ContentGenerationError, match="exceeds its 280-character limit"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["x"]})
