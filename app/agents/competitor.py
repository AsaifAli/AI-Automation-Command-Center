from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config
from app.integrations.intelligence import IntelligenceSource


class CompetitorAgent(Agent):
    name = "competitor"

    def __init__(self, llm, intelligence: IntelligenceSource | None = None):
        super().__init__(llm)
        self.intelligence = intelligence or IntelligenceSource(llm.settings)

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("competitor", {})
        competitors = payload.get("competitors") or defaults.get("competitors", [])
        sources = payload.get("sources") or defaults.get("sources", [])
        feed_items = self.intelligence.collect(sources) if sources else []

        items = []
        for name in competitors[:20]:
            related = [item for item in feed_items if name.lower() in item["title"].lower()]
            items.append(
                {
                    "entity": name,
                    "signals": ["product narrative", "community activity", "partnership messaging"],
                    "priority": "medium" if not related else "high",
                    "observed_items": related[:5],
                    "implication": "Validate high-signal activity against primary sources before acting.",
                }
            )

        llm_note = self.llm.generate(
            "You are a competitive intelligence analyst. Avoid unsupported claims and distinguish observed evidence from inference.",
            f"Competitors: {competitors}; observed feed items: {feed_items[:10]}",
        )
        return {
            "competitors": items,
            "source_count": len(sources),
            "observed_item_count": len(feed_items),
            "analyst_note": llm_note["text"],
            "recommended_actions": [
                "Review high-signal activity",
                "Validate claims with primary sources",
                "Convert insights into measurable experiments",
            ],
            "_usage": llm_note.get("usage", {}),
        }
