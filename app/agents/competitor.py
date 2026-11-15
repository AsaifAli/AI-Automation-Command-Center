"""Evidence-oriented competitor intelligence with explicit coverage limits."""

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
        competitors = [str(item).strip() for item in competitors[:20] if str(item).strip()]
        sources = payload.get("sources") or defaults.get("sources", [])
        feed_items = self.intelligence.collect(sources) if sources else []

        items = []
        for name in competitors:
            related = [
                item for item in feed_items
                if name.casefold() in str(item.get("title", "")).casefold()
            ]
            if related:
                signals = [
                    f"Title match in configured feed: {str(item.get('title', 'Untitled'))[:240]}"
                    for item in related[:5]
                ]
                priority = "review"
                implication = (
                    "One or more configured feed titles matched this entity. "
                    "This is a discovery signal only; inspect the source before drawing conclusions."
                )
            else:
                signals = []
                priority = "unverified"
                implication = (
                    "No matching items were observed in the configured feeds. "
                    "This does not establish that the competitor was inactive."
                )

            items.append({
                "entity": name,
                "signals": signals,
                "priority": priority,
                "observed_items": related[:5],
                "implication": implication,
                "evidence_basis": "case-insensitive competitor-name match in feed title only",
            })

        llm_note = self.llm.generate(
            (
                "You are a competitive intelligence analyst. Distinguish observed evidence from hypotheses. "
                "Do not infer product launches, partnerships, traction or strategy from a title-only match. "
                "When evidence is absent, say that coverage is insufficient rather than inventing findings."
            ),
            f"Competitors: {competitors}; observed feed items: {feed_items[:10]}; matched records: {items}",
        )
        return {
            "competitors": items,
            "source_count": len(sources),
            "observed_item_count": len(feed_items),
            "analyst_note": llm_note["text"],
            "recommended_actions": [
                "Open and validate matching primary sources",
                "Record only claims supported by the source content",
                "Turn validated signals into measurable experiments",
            ],
            "coverage_note": "RSS/Atom feed-title matching is not comprehensive market monitoring.",
            "_usage": llm_note.get("usage", {}),
        }
