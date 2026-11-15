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

        total = len(normalized)
        risk_count = len(risks)
        on_track = total - risk_count
        if not total:
            leadership_brief = "No KPI updates were supplied. Add reporting data before drawing performance conclusions."
        elif risk_count:
            leadership_brief = (
                f"{total} update(s) were reviewed: {on_track} on track and {risk_count} at risk. "
                "Resolve or assign the reported blockers before treating the period as on plan."
            )
        else:
            leadership_brief = (
                f"{total} update(s) were reviewed and no blockers were reported. "
                "Confirm the source data and reporting owners before using this as an executive status."
            )

        llm_note = self.llm.generate(
            (
                "You are a leadership briefing assistant. Highlight risks, decisions, owners and missing evidence. "
                "Do not invent targets, baselines, progress percentages or deadlines. Distinguish reported values "
                "from recommendations and state when the input is insufficient."
            ),
            f"Updates: {normalized}",
        )
        return {
            "normalized_updates": normalized,
            "risk_count": risk_count,
            "risks": risks,
            "leadership_brief": leadership_brief,
            "executive_note": llm_note["text"],
            "_usage": llm_note.get("usage", {}),
        }
