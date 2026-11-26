import json

import pytest

from app.agents.content import CHANNEL_LIMITS, CHANNEL_TARGETS, ContentAgent, ContentGenerationError
from app.core.config import Settings


class FakeLiveLLM:
    def __init__(self, text):
        self.responses = list(text) if isinstance(text, list) else [text]
        self.calls = []
        self.response_formats = []
        self.settings = Settings(
            app_env="development",
            api_auth_enabled=False,
            demo_mode=False,
        )
        self.gateway_active = False

    @property
    def gateway_session_active(self):
        return self.gateway_active

    def generate(self, system, user, *, response_format=None):
        self.calls.append((system, user))
        self.response_formats.append(response_format)
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
    schema = llm.response_formats[0]["json_schema"]
    assert llm.response_formats[0]["type"] == "json_schema"
    assert schema["strict"] is True
    variants = schema["schema"]["properties"]["posts"]["items"]["anyOf"]
    assert variants[0]["properties"]["topic"]["enum"] == [topic]
    assert variants[0]["properties"]["channel"]["enum"] == ["linkedin"]
    assert variants[0]["properties"]["draft"]["maxLength"] == CHANNEL_LIMITS["linkedin"]


def test_live_content_requires_every_requested_topic_channel_pair():
    llm = FakeLiveLLM(json.dumps({"posts": [], "strategy_note": "No posts"}))

    with pytest.raises(ContentGenerationError, match="omitted 1"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["linkedin"]})

    assert len(llm.calls) == 2
    assert "validation_error" in json.loads(llm.calls[1][1])["correction"]


def test_live_content_rejects_invalid_json_instead_of_fabricating_drafts():
    llm = FakeLiveLLM("This is not JSON")

    with pytest.raises(ContentGenerationError, match="valid structured output"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["linkedin"]})

    assert len(llm.calls) == 2
    assert "validation_error" in json.loads(llm.calls[1][1])["correction"]


def test_live_content_extracts_json_from_fenced_or_prefaced_model_output():
    topic = "AI operations"
    response = "Here is the requested JSON:\n```json\n" + _response(
        topic, "linkedin", "A concise draft for AI operations."
    ) + "\n```"
    llm = FakeLiveLLM(response)

    result = ContentAgent(llm).run({"topics": [topic], "channels": ["linkedin"]})

    assert result["posts"][0]["draft"] == "A concise draft for AI operations."
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


def test_live_content_repairs_invalid_structured_output_once():
    topic = "AI automation"
    valid = _response(topic, "linkedin", "A structured draft for AI automation.")
    llm = FakeLiveLLM(["The response should be JSON, but this is not.", valid])

    result = ContentAgent(llm).run({"topics": [topic], "channels": ["linkedin"]})

    assert result["posts"][0]["draft"] == "A structured draft for AI automation."
    assert len(llm.calls) == 2
    correction = json.loads(llm.calls[1][1])["correction"]
    assert "validation_error" in correction
    assert llm.response_formats[0] == llm.response_formats[1]


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



def test_live_content_schema_enforces_each_channel_hard_character_limit():
    topic = "AI agents in enterprise finance operations, including reconciliation and exception handling"
    llm = FakeLiveLLM(json.dumps({
        "posts": [
            {"topic": topic, "channel": channel, "draft": f"A concise {channel} draft."}
            for channel in ("x", "linkedin", "telegram")
        ],
        "strategy_note": "Adapt each channel to its format.",
    }))

    ContentAgent(llm).run({"topics": [topic], "channels": ["x", "linkedin", "telegram"]})

    variants = llm.response_formats[0]["json_schema"]["schema"]["properties"]["posts"]["items"]["anyOf"]
    limits_by_channel = {
        variant["properties"]["channel"]["enum"][0]: variant["properties"]["draft"]["maxLength"]
        for variant in variants
    }
    assert limits_by_channel == CHANNEL_LIMITS


def test_live_content_repairs_a_389_character_x_draft_with_a_second_bounded_attempt():
    topic = "AI agents in enterprise finance operations, including reconciliation and exception handling"
    too_long_first = "X" * 389
    too_long_second = "Y" * 315
    short_x_draft = (
        "AI agents match finance transactions, flag reconciliation exceptions, "
        "and route unusual cases for human review."
    )
    llm = FakeLiveLLM([
        _response(topic, "x", too_long_first),
        _response(topic, "x", too_long_second),
        _response(topic, "x", short_x_draft),
    ])

    result = ContentAgent(llm).run({"topics": [topic], "channels": ["x"]})

    assert len(llm.calls) == 3
    assert result["posts"][0]["draft"] == short_x_draft
    assert len(short_x_draft) <= CHANNEL_TARGETS["x"]
    first_repair = json.loads(llm.calls[1][1])["correction"]
    second_repair = json.loads(llm.calls[2][1])["correction"]
    assert first_repair["violations"][0]["actual_characters"] == 389
    assert second_repair["violations"][0]["actual_characters"] == 315
    assert "rewrite only the listed over-limit draft" in second_repair["instruction"].lower()
    assert all(
        llm.response_formats[0] == response_format
        for response_format in llm.response_formats[1:]
    )
    assert result["_usage"]["prompt_tokens"] == 30
    assert result["_usage"]["completion_tokens"] == 15
    assert result["_usage"]["total_tokens"] == 45
    assert result["_usage"]["estimated_cost_usd"] == 0.003


def test_live_content_fails_closed_if_repair_still_exceeds_channel_limit():
    over_limit = _response("AI", "x", "x" * (CHANNEL_LIMITS["x"] + 1))
    llm = FakeLiveLLM([over_limit, over_limit, over_limit])

    with pytest.raises(ContentGenerationError, match="exceeds its 280-character limit"):
        ContentAgent(llm).run({"topics": ["AI"], "channels": ["x"]})

    assert len(llm.calls) == 3
