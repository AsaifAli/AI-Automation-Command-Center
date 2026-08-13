from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config


class OutreachAgent(Agent):
    name = "outreach"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("outreach", {})
        candidates = payload.get("candidates") or defaults.get("candidates", [])
        qualified = []
        for candidate in candidates[:25]:
            name = candidate.get("name", "Unknown")
            context = candidate.get("context", "strategic collaboration")
            qualified.append(
                {
                    "name": name,
                    "qualification": "review",
                    "reason": "Requires human validation of fit, identity, and relationship context.",
                    "channel": candidate.get("channel", "email_or_social"),
                    "message": f"Hi {name}, I noticed the work around {context}. I would value a short conversation about a potential collaboration.",
                    "approval_required": True,
                    "follow_up_days": 5,
                }
            )
        llm_note = self.llm.generate(
            "You are a partnership qualification assistant. Never claim a relationship exists without evidence.",
            f"Candidates: {candidates}",
        )
        return {
            "candidates": qualified,
            "qualification_note": llm_note["text"],
            "policy": "No autonomous external sending; approval is required.",
            "_usage": llm_note.get("usage", {}),
        }
