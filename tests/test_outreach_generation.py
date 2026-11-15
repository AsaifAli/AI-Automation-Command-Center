import json

import pytest

from app.agents.outreach import OutreachAgent, OutreachGenerationError
from app.core.config import Settings


class FakeLiveLLM:
    def __init__(self, text):
        self.text = text
        self.settings = Settings(app_env="development", api_auth_enabled=False, demo_mode=False)
        self.calls = []
        self.gateway_session_active = False

    def generate(self, system, user):
        self.calls.append((system, user))
        return {
            "text": self.text,
            "usage": {"provider": "test", "model": "test-model", "total_tokens": 25},
        }


def test_live_outreach_messages_are_model_generated_and_approval_gated():
    text = json.dumps({
        "messages": [{
            "name": "Acme Partner",
            "channel": "linkedin",
            "message": "Hello Acme Partner, would you be open to a short conversation about the AI infrastructure collaboration context you shared?",
        }],
        "qualification_note": "The supplied context is limited; verify fit before contact.",
    })
    llm = FakeLiveLLM(text)
    result = OutreachAgent(llm).run({
        "candidates": [{"name": "Acme Partner", "channel": "linkedin", "context": "AI infrastructure collaboration"}]
    })
    item = result["candidates"][0]
    assert item["message"].startswith("Hello Acme Partner")
    assert item["approval_required"] is True
    assert result["policy"].startswith("No autonomous external sending")
    assert result["_usage"]["provider"] == "test"


def test_live_outreach_rejects_missing_candidate_messages():
    llm = FakeLiveLLM(json.dumps({"messages": [], "qualification_note": "None"}))
    with pytest.raises(OutreachGenerationError, match="omitted 1"):
        OutreachAgent(llm).run({"candidates": [{"name": "Acme"}]})


def test_live_outreach_rejects_channel_changes():
    llm = FakeLiveLLM(json.dumps({
        "messages": [{"name": "Acme", "channel": "email", "message": "Hello"}],
        "qualification_note": "Review first.",
    }))
    with pytest.raises(OutreachGenerationError, match="changed a candidate's selected channel"):
        OutreachAgent(llm).run({"candidates": [{"name": "Acme", "channel": "linkedin"}]})
