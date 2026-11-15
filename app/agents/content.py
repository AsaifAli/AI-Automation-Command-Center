"""LLM-backed, approval-gated social content generation."""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config

CHANNEL_LIMITS = {"x": 280, "linkedin": 1300, "telegram": 800}


class ContentGenerationError(ValueError):
    """Raised when the content model returns an invalid or incomplete payload."""


class ContentAgent(Agent):
    name = "content"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("content", {})
        topics = [str(item).strip() for item in (payload.get("topics") or defaults.get("topics") or []) if str(item).strip()]
        channels = [str(item).strip().lower() for item in (payload.get("channels") or defaults.get("channels") or ["linkedin"]) if str(item).strip()]
        tone = str(payload.get("tone") or defaults.get("tone") or "insightful").strip()

        if not topics:
            topics = ["AI automation"]
        if not channels:
            channels = ["linkedin"]

        topics = topics[:10]
        channels = channels[:5]
        unsupported = sorted(set(channels) - set(CHANNEL_LIMITS))
        if unsupported:
            raise ContentGenerationError(
                f"Unsupported content channel(s): {', '.join(unsupported)}"
            )

        if self.llm.settings.demo_mode and not getattr(self.llm, "gateway_session_active", False):
            posts = self._demo_posts(topics, channels, tone)
            llm_note = self.llm.generate(
                "You are a content strategy assistant. Keep recommendations concise and operational.",
                f"Topics: {topics}; channels: {channels}; tone: {tone}",
            )
        else:
            request = {
                "topics": topics,
                "channels": channels,
                "tone": tone,
                "requirements": [
                    "Write original, useful content; do not just restate the topic.",
                    "Tailor each draft to its channel and respect that channel's character limit.",
                    "Use concrete examples and practical insight where appropriate.",
                    "Do not invent statistics, customer names, or factual claims.",
                    "Every draft must remain a draft for human review; do not publish or claim it was published.",
                ],
                "character_limits": CHANNEL_LIMITS,
                "response_schema": {
                    "posts": [
                        {"topic": "one exact input topic", "channel": "one requested channel", "draft": "complete post text"}
                    ],
                    "strategy_note": "short generation rationale or editorial note",
                },
            }
            llm_note = self.llm.generate(
                (
                    "You are an expert multi-channel content strategist. Generate complete, distinct, "
                    "ready-for-human-review social posts. Return only one valid JSON object matching "
                    "the requested schema. Include exactly one post for every topic/channel pair. "
                    "Preserve each topic exactly as supplied; never split one brief into fragments. "
                    "Do not wrap the JSON in Markdown fences."
                ),
                json.dumps(request, ensure_ascii=False),
            )
            posts, strategy_note = self._parse_model_response(
                llm_note.get("text", ""), topics, channels
            )
            llm_note["text"] = strategy_note

        for post in posts:
            post["review_status"] = "needs_review"
            post["publish_action"] = "human_approval_required"

        return {
            "posts": posts,
            "count": len(posts),
            "strategy_note": llm_note["text"],
            "next_step": "Human review before publishing.",
            "_usage": llm_note.get("usage", {}),
        }

    @staticmethod
    def _parse_model_response(
        response_text: str,
        topics: list[str],
        channels: list[str],
    ) -> tuple[list[dict[str, str]], str]:
        text = str(response_text or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContentGenerationError(
                "The content model did not return valid structured output. Retry the workflow."
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
            raise ContentGenerationError(
                "The content model response is missing the required posts list."
            )

        expected = {(topic, channel) for topic in topics for channel in channels}
        seen: set[tuple[str, str]] = set()
        parsed: dict[tuple[str, str], dict[str, str]] = {}

        for item in payload["posts"]:
            if not isinstance(item, dict):
                raise ContentGenerationError("The content model returned an invalid post item.")
            topic = item.get("topic")
            channel = item.get("channel")
            draft = item.get("draft")
            if not isinstance(topic, str) or not isinstance(channel, str):
                raise ContentGenerationError(
                    "The content model returned an invalid topic or channel."
                )
            pair = (topic, channel)
            if pair not in expected or pair in seen:
                raise ContentGenerationError(
                    "The content model returned an unknown or duplicate topic/channel pair."
                )
            if not isinstance(draft, str) or not draft.strip():
                raise ContentGenerationError("The content model returned an empty draft.")
            seen.add(pair)
            clean_draft = draft.strip()
            if len(clean_draft) > CHANNEL_LIMITS[channel]:
                raise ContentGenerationError(
                    f"The {channel} draft for one topic exceeds its {CHANNEL_LIMITS[channel]}-character limit. Retry with a shorter brief."
                )
            parsed[pair] = {
                "topic": topic,
                "channel": channel,
                "draft": clean_draft,
            }

        missing = expected - seen
        if missing:
            raise ContentGenerationError(
                f"The content model omitted {len(missing)} requested topic/channel draft(s)."
            )

        ordered_posts = [
            parsed[(topic, channel)]
            for topic in topics
            for channel in channels
        ]
        strategy_note = payload.get("strategy_note", "")
        if not isinstance(strategy_note, str):
            strategy_note = ""
        if not strategy_note.strip():
            strategy_note = "Generated drafts are ready for human review."
        return ordered_posts, strategy_note.strip()

    @staticmethod
    def _demo_posts(
        topics: list[str],
        channels: list[str],
        tone: str,
    ) -> list[dict[str, str]]:
        posts = []
        for topic in topics:
            for channel in channels:
                limit = CHANNEL_LIMITS[channel]
                draft = (
                    f"{topic}: a practical view of how intelligent automation turns "
                    f"repetitive operations into measurable leverage. Tone: {tone}. "
                    "[Demo draft — connect an LLM provider for generated content.]"
                )
                posts.append({"topic": topic, "channel": channel, "draft": draft[:limit]})
        return posts
