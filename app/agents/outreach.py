"""Approval-gated outreach drafting with explicit provenance and safe fallbacks."""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import Agent
from app.core.demo import load_demo_config


class OutreachGenerationError(ValueError):
    """Raised when the model does not return a complete, safe outreach draft set."""


class OutreachAgent(Agent):
    name = "outreach"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = load_demo_config(self.llm.settings).get("outreach", {})
        candidates = payload.get("candidates") or defaults.get("candidates", [])
        candidates = [dict(item) for item in candidates[:25] if isinstance(item, dict)]
        if not candidates:
            return {
                "candidates": [],
                "qualification_note": "No outreach candidates were supplied.",
                "policy": "No autonomous external sending; approval is required.",
                "_usage": {},
            }

        safe_candidates = []
        seen_names: set[str] = set()
        for candidate in candidates:
            name = str(candidate.get("name", "Unknown")).strip()[:160] or "Unknown"
            context = str(candidate.get("context", "strategic collaboration")).strip()[:1000]
            channel = str(candidate.get("channel", "email")).strip().lower() or "email"
            if name.casefold() in seen_names:
                raise OutreachGenerationError("Candidate names must be unique within an outreach run.")
            seen_names.add(name.casefold())
            safe_candidates.append({
                "name": name,
                "context": context,
                "channel": channel,
                "type": str(candidate.get("type", "partner")).strip()[:80],
            })

        if self.llm.settings.demo_mode and not getattr(self.llm, "gateway_session_active", False):
            messages = {
                candidate["name"]: (
                    f"Hello {candidate['name']}, I came across your work related to "
                    f"{candidate['context'] or 'your current initiatives'}. "
                    "Would you be open to a brief conversation to explore whether there may be a useful fit?"
                )
                for candidate in safe_candidates
            }
            llm_note = self.llm.generate(
                "You are a partnership qualification assistant. Keep the demo note concise and never claim an existing relationship.",
                f"Candidates: {safe_candidates}",
            )
            qualification_note = llm_note["text"]
        else:
            request = {
                "candidates": safe_candidates,
                "requirements": [
                    "Write one complete, concise outreach message for every supplied candidate.",
                    "Preserve candidate names and channels exactly as supplied.",
                    "Use only the supplied context; do not claim prior contact, a relationship, a meeting, or knowledge not present in the input.",
                    "Use a professional, relevant, low-pressure tone and a clear next step.",
                    "Do not send messages or claim they have been sent.",
                ],
                "response_schema": {
                    "messages": [
                        {"name": "exact candidate name", "channel": "exact supplied channel", "message": "complete message draft"}
                    ],
                    "qualification_note": "brief note about assumptions and human review",
                },
            }
            llm_note = self.llm.generate(
                (
                    "You are an expert partnership outreach writer. Return only one valid JSON object matching "
                    "the requested schema. Include exactly one message for every candidate, do not invent context, "
                    "and never imply that any message has been sent."
                ),
                json.dumps(request, ensure_ascii=False),
            )
            messages, qualification_note = self._parse_model_response(
                llm_note.get("text", ""),
                safe_candidates,
            )
            llm_note["text"] = qualification_note

        qualified = []
        for candidate in safe_candidates:
            message = messages[candidate["name"]]
            qualified.append({
                "name": candidate["name"],
                "qualification": "review",
                "reason": "Verify identity, fit, and the supplied relationship context before contacting this person.",
                "channel": candidate["channel"],
                "message": message,
                "approval_required": True,
                "follow_up_days": 5,
            })

        return {
            "candidates": qualified,
            "qualification_note": qualification_note,
            "policy": "No autonomous external sending; every message requires an authorized human approval.",
            "_usage": llm_note.get("usage", {}),
        }

    @staticmethod
    def _parse_model_response(
        response_text: str,
        candidates: list[dict[str, str]],
    ) -> tuple[dict[str, str], str]:
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
            raise OutreachGenerationError("The outreach model did not return valid structured output.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise OutreachGenerationError("The outreach model response is missing the required messages list.")

        expected = {candidate["name"]: candidate["channel"] for candidate in candidates}
        generated: dict[str, str] = {}
        for item in payload["messages"]:
            if not isinstance(item, dict):
                raise OutreachGenerationError("The outreach model returned an invalid message item.")
            name = item.get("name")
            channel = item.get("channel")
            message = item.get("message")
            if not isinstance(name, str) or name not in expected:
                raise OutreachGenerationError("The outreach model returned an unknown candidate.")
            if name in generated:
                raise OutreachGenerationError("The outreach model returned a duplicate candidate message.")
            if channel != expected[name]:
                raise OutreachGenerationError("The outreach model changed a candidate's selected channel.")
            if not isinstance(message, str) or not message.strip() or len(message.strip()) > 3000:
                raise OutreachGenerationError("The outreach model returned an empty or oversized message.")
            generated[name] = message.strip()

        missing = set(expected) - set(generated)
        if missing:
            raise OutreachGenerationError(f"The outreach model omitted {len(missing)} candidate message(s).")

        note = payload.get("qualification_note", "")
        if not isinstance(note, str):
            note = ""
        if not note.strip():
            note = "Verify each candidate's identity, fit, and context before external contact."
        return generated, note.strip()
