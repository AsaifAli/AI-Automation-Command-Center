from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config


class ContentAgent(Agent):
    name = "content"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("content", {})
        topics = payload.get("topics") or defaults.get("topics", ["AI automation"])
        channels = payload.get("channels") or defaults.get("channels", ["linkedin", "x", "telegram"])
        tone = payload.get("tone") or defaults.get("tone", "insightful")
        posts = []
        for topic in topics[:10]:
            for channel in channels[:5]:
                limit = {"x": 280, "linkedin": 1300, "telegram": 800}.get(channel, 800)
                text = (
                    f"{topic}: a practical view of how intelligent automation turns repetitive operations "
                    f"into measurable leverage. Tone: {tone}."
                )
                posts.append(
                    {
                        "topic": topic,
                        "channel": channel,
                        "draft": text[:limit],
                        "review_status": "needs_review",
                        "publish_action": "human_approval_required",
                    }
                )
        llm_note = self.llm.generate(
            "You are a content strategy assistant. Keep recommendations concise and operational.",
            f"Topics: {topics}; channels: {channels}; tone: {tone}",
        )
        return {
            "posts": posts,
            "count": len(posts),
            "strategy_note": llm_note["text"],
            "next_step": "Human review before publishing.",
            "_usage": llm_note.get("usage", {}),
        }
