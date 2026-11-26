"""LLM-backed, approval-gated social content generation."""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config

# Hard limits are enforced locally. The lower targets leave room for model
# variation and reduce the chance that a generated draft needs correction.
CHANNEL_LIMITS = {"x": 280, "linkedin": 1300, "telegram": 800}
CHANNEL_TARGETS = {"x": 220, "linkedin": 1200, "telegram": 700}
MAX_CHARACTER_REPAIR_ATTEMPTS = 2


class ContentGenerationError(ValueError):
    """Raised when the content model returns an invalid or incomplete payload."""


class ContentCharacterLimitError(ContentGenerationError):
    """Raised when one or more valid drafts exceed a channel's hard character limit."""

    def __init__(self, violations: list[dict[str, Any]]):
        self.violations = violations
        details = "; ".join(
            f"The {item['channel']} draft for topic {item['topic']!r} exceeds its "
            f"{item['character_limit']}-character limit "
            f"({item['actual_characters']} characters)."
            for item in violations
        )
        super().__init__(details)


class ContentAgent(Agent):
    name = "content"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("content", {})
        topic_values = payload.get("topics") or defaults.get("topics") or []
        channel_values = payload.get("channels") or defaults.get("channels") or ["linkedin"]
        topics = [str(item).strip() for item in topic_values if str(item).strip()]
        channels = [
            str(item).strip().lower()
            for item in channel_values
            if str(item).strip()
        ]
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

        if (
            self.llm.settings.demo_mode
            and not getattr(self.llm, "gateway_session_active", False)
        ):
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
                    "Tailor each draft to its channel and stay within the preferred character target for that channel.",
                    ("Treat the supplied hard character limits as absolute; count the complete draft, "
                    "including spaces and punctuation."),
                    "Use concrete examples and practical insight where appropriate.",
                    "Do not invent statistics, customer names, or factual claims.",
                    "Every draft must remain a draft for human review; do not publish or claim it was published.",
                ],
                "hard_character_limits": CHANNEL_LIMITS,
                "preferred_character_targets": CHANNEL_TARGETS,
                "response_schema": {
                    "posts": [
                        {
                            "topic": "one exact input topic",
                            "channel": "one requested channel",
                            "draft": "complete post text",
                        }
                    ],
                    "strategy_note": "short generation rationale or editorial note",
                },
            }
            system_prompt = (
                "You are an expert multi-channel content strategist. Generate complete, distinct, "
                "ready-for-human-review social posts. Return only one valid JSON object matching "
                "the requested schema. Include exactly one post for every topic/channel pair. "
                "Preserve each topic exactly as supplied; never split one brief into fragments. "
                "Observe the preferred character targets and never exceed any hard character limit. "
                "Do not wrap the JSON in Markdown fences."
            )
            response_format = self._response_format(topics, channels)
            llm_note = self.llm.generate(
                system_prompt,
                json.dumps(request, ensure_ascii=False),
                response_format=response_format,
            )
            response_text = llm_note.get("text", "")
            try:
                posts, strategy_note = self._parse_model_response(
                    response_text, topics, channels
                )
            except ContentCharacterLimitError as exc:
                # A provider can ignore a prose limit even with structured output.
                # Retry only the invalid drafts, and cap repairs to avoid an
                # unbounded loop or surprise usage when a model keeps misbehaving.
                aggregate_usage = llm_note.get("usage", {})
                previous_response = response_text
                latest_error = exc
                for attempt in range(1, MAX_CHARACTER_REPAIR_ATTEMPTS + 1):
                    repair_request = {
                        **request,
                        "correction": {
                            "instruction": (
                                "Rewrite only the listed over-limit draft(s). Keep every already-valid "
                                "draft unchanged. Return the complete JSON response with exactly one "
                                "draft for every requested topic/channel pair. For each listed draft, "
                                "stay at or below its hard character limit and aim for its preferred "
                                "target. Count all characters, including spaces and punctuation. "
                                "Do not truncate with ellipses, omit a draft, or change the supplied topic. "
                                f"This is shortening attempt {attempt} of {MAX_CHARACTER_REPAIR_ATTEMPTS}."
                            ),
                            "violations": latest_error.violations,
                            "previous_response": previous_response,
                        },
                    }
                    correction_note = self.llm.generate(
                        system_prompt + (
                            " The previous output exceeded a channel character limit. Shorten only "
                            "the offending draft(s), obey the schema's per-channel maxLength, and "
                            "preserve all already-valid drafts."
                        ),
                        json.dumps(repair_request, ensure_ascii=False),
                        response_format=response_format,
                    )
                    aggregate_usage = self._combine_usage(
                        aggregate_usage, correction_note.get("usage")
                    )
                    previous_response = correction_note.get("text", "")
                    try:
                        posts, strategy_note = self._parse_model_response(
                            previous_response, topics, channels
                        )
                    except ContentCharacterLimitError as next_error:
                        latest_error = next_error
                        if attempt == MAX_CHARACTER_REPAIR_ATTEMPTS:
                            raise
                        continue

                    llm_note = dict(correction_note)
                    llm_note["usage"] = aggregate_usage
                    break
                else:
                    raise latest_error
            except ContentGenerationError as exc:
                # Providers can still return malformed JSON despite a strict
                # schema request. Give the model one bounded opportunity to
                # regenerate the complete payload; never accept partial output.
                repair_request = {
                    **request,
                    "correction": {
                        "instruction": (
                            "Your previous response failed structured-output validation. "
                            "Regenerate the entire response as one valid JSON object matching "
                            "the required schema. Include every requested topic/channel pair exactly once. "
                            "Return no Markdown, comments, or prose outside the JSON object."
                        ),
                        "validation_error": str(exc)[:500],
                    },
                }
                correction_note = self.llm.generate(
                    system_prompt + " The previous response failed strict structured-output validation. Return valid JSON only.",
                    json.dumps(repair_request, ensure_ascii=False),
                    response_format=response_format,
                )
                posts, strategy_note = self._parse_model_response(
                    correction_note.get("text", ""), topics, channels
                )
                correction_note = dict(correction_note)
                correction_note["usage"] = self._combine_usage(
                    llm_note.get("usage"), correction_note.get("usage")
                )
                llm_note = correction_note

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
    def _response_format(topics: list[str], channels: list[str]) -> dict[str, Any]:
        """Request a strict JSON schema through OpenAI-compatible Gateway providers."""
        channel_variants = []
        for channel in channels:
            channel_variants.append(
                {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "enum": topics},
                        "channel": {"type": "string", "enum": [channel]},
                        "draft": {
                            "type": "string",
                            "maxLength": CHANNEL_LIMITS[channel],
                        },
                    },
                    "required": ["topic", "channel", "draft"],
                    "additionalProperties": False,
                }
            )

        return {
            "type": "json_schema",
            "json_schema": {
                "name": "flowpilot_content_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "posts": {
                            "type": "array",
                            "items": {"anyOf": channel_variants},
                        },
                        "strategy_note": {"type": "string"},
                    },
                    "required": ["posts", "strategy_note"],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _combine_usage(first: Any, second: Any) -> dict[str, Any]:
        """Aggregate token and cost metadata across the initial and repair calls."""
        first_usage = first if isinstance(first, dict) else {}
        second_usage = second if isinstance(second, dict) else {}
        combined = dict(second_usage)
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            combined[field] = int(first_usage.get(field, 0) or 0) + int(second_usage.get(field, 0) or 0)
        combined["estimated_cost_usd"] = round(
            float(first_usage.get("estimated_cost_usd", 0) or 0)
            + float(second_usage.get("estimated_cost_usd", 0) or 0),
            8,
        )
        if "cost_estimate_available" in first_usage or "cost_estimate_available" in second_usage:
            combined["cost_estimate_available"] = bool(
                first_usage.get("cost_estimate_available", True)
                and second_usage.get("cost_estimate_available", True)
            )
        return combined

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
            # Some models add a short preamble or Markdown fence even when
            # instructed to return JSON. Extract one complete object only;
            # downstream schema/pair validation remains strict.
            decoder = json.JSONDecoder()
            payload = None
            for index, character in enumerate(text):
                if character != "{":
                    continue
                try:
                    candidate, _ = decoder.raw_decode(text[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    payload = candidate
                    break
            if payload is None:
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
        violations: list[dict[str, Any]] = []

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
            hard_limit = CHANNEL_LIMITS[channel]
            if len(clean_draft) > hard_limit:
                violations.append({
                    "topic": topic,
                    "channel": channel,
                    "actual_characters": len(clean_draft),
                    "character_limit": hard_limit,
                    "preferred_target": CHANNEL_TARGETS[channel],
                })
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
        if violations:
            raise ContentCharacterLimitError(violations)

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
