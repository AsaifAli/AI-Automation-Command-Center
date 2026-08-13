from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config


class KPIAgent(Agent):
    name = "kpi"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("kpi", {})
        updates = payload.get("updates") or defaults.get("updates", [])
        normalized = []
        risks = []
        for update in updates[:100]:
            blocker = str(update.get("blocker", "")).strip()
            record = {
                "entity": update.get("entity", "Unknown"),
                "metric": update.get("metric", "Unspecified"),
                "value": update.get("value", "Not reported"),
                "blocker": blocker or "None reported",
                "status": "at_risk" if blocker and blocker.lower() not in {"none", "none reported", "n/a"} else "on_track",
            }
            normalized.append(record)
            if record["status"] == "at_risk":
                risks.append(record)
        llm_note = self.llm.generate(
            "You are a leadership briefing assistant. Highlight risks, decisions, and missing evidence.",
            f"Updates: {normalized}",
        )
        return {
            "normalized_updates": normalized,
            "risk_count": len(risks),
            "risks": risks,
            "leadership_brief": "Progress is summarized from submitted updates. Validate source data before executive decisions.",
            "executive_note": llm_note["text"],
            "_usage": llm_note.get("usage", {}),
        }
