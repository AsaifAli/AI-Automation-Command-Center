"""Pure formatting helpers for client-facing FlowPilot reports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any


def humanize_key(value: Any) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return text.strip().title() or "Detail"


def parse_model_note(value: Any) -> dict[str, Any] | str:
    """Decode model notes that were returned as JSON strings without exposing raw JSON."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return {"Highlights": list(value)}
    if not isinstance(value, str):
        return str(value or "")

    note = value.strip()
    if not note:
        return ""
    candidate = note
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return note
    if isinstance(parsed, Mapping):
        return dict(parsed)
    if isinstance(parsed, list):
        return {"Highlights": parsed}
    return str(parsed)


def display_value(value: Any) -> str:
    if value is None:
        return "Not provided"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        parts = [
            f"{humanize_key(key)}: {display_value(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {})
        ]
        return " · ".join(parts) or "No details supplied"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = [display_value(item) for item in value]
        return "; ".join(part for part in parts if part) or "None"
    return str(value).strip()


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe_report_text(value: Any) -> str:
    """Keep report output readable while preventing accidental code-fence injection."""
    text = display_value(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def build_client_report_markdown(
    workflow: str,
    output: Mapping[str, Any] | Any,
    run: Mapping[str, Any] | None = None,
) -> str:
    """Build a polished, human-readable Markdown deliverable (never raw JSON)."""
    data = dict(output) if isinstance(output, Mapping) else {"result": output}
    metadata = dict(run or {})
    workflow = str(workflow or metadata.get("workflow") or "workflow").lower()
    title = {
        "content": "Content Studio — Draft Pack",
        "competitor": "Competitor Intelligence Brief",
        "outreach": "Partnership Outreach Brief",
        "kpi": "Performance & Risk Briefing",
    }.get(workflow, f"{humanize_key(workflow)} Report")

    lines = [f"# {title}", ""]
    status = metadata.get("status")
    created = metadata.get("created_at")
    if status:
        lines.append(f"**Execution status:** {humanize_key(status)}")
    if created:
        lines.append(f"**Generated:** {_safe_report_text(created)}")
    if metadata.get("run_id"):
        lines.append(f"**Reference:** `{_safe_report_text(metadata['run_id'])}`")
    if status or created or metadata.get("run_id"):
        lines.append("")

    if workflow == "content":
        posts = _list_of_mappings(data.get("posts"))
        lines.extend([f"**Drafts prepared:** {len(posts)}", "", "## Drafts", ""])
        for index, post in enumerate(posts, 1):
            channel = humanize_key(post.get("channel", "channel"))
            lines.extend([
                f"### Draft {index} — {channel}",
                f"**Brief:** {_safe_report_text(post.get('topic', ''))}",
                "",
                _safe_report_text(post.get("draft", "")),
                "",
                "_Review state: human review required. No content has been published._",
                "",
            ])
        note = parse_model_note(data.get("strategy_note", ""))
        if note:
            lines.extend(["## Editorial Direction", _safe_report_text(note), ""])
        lines.extend(["## Publishing Controls", "All drafts remain unapproved until an authorized reviewer approves them."])

    elif workflow == "competitor":
        competitors = _list_of_mappings(data.get("competitors"))
        lines.extend([
            "## Coverage",
            f"- Competitors reviewed: {len(competitors)}",
            f"- Configured feeds: {data.get('source_count', 0)}",
            f"- Feed items observed: {data.get('observed_item_count', 0)}",
            "",
            "## Findings",
            "",
        ])
        for item in competitors:
            lines.extend([
                f"### {_safe_report_text(item.get('entity', 'Competitor'))}",
                f"**Evidence status:** {humanize_key(item.get('priority', 'unverified'))}",
                _safe_report_text(item.get("implication", "")),
                "",
            ])
            signals = item.get("signals") or []
            if signals:
                lines.extend(["**Signals / checks**", *[f"- {_safe_report_text(signal)}" for signal in signals], ""])
            observed = _list_of_mappings(item.get("observed_items"))
            if observed:
                lines.append("**Observed feed items**")
                for source in observed:
                    lines.append(f"- {_safe_report_text(source.get('title', 'Untitled'))} ({_safe_report_text(source.get('source', 'Feed'))})")
                lines.append("")
        note = parse_model_note(data.get("analyst_note", ""))
        if note:
            lines.extend(["## Analyst Assessment", _safe_report_text(note), ""])
        actions = data.get("recommended_actions") or []
        if actions:
            lines.extend(["## Recommended Next Steps", *[f"- {_safe_report_text(action)}" for action in actions], ""])
        lines.append("_Treat this brief as directional intelligence. Validate claims against primary sources before making decisions._")

    elif workflow == "outreach":
        candidates = _list_of_mappings(data.get("candidates"))
        lines.extend([f"**Opportunities reviewed:** {len(candidates)}", "", "## Outreach Drafts", ""])
        for item in candidates:
            lines.extend([
                f"### {_safe_report_text(item.get('name', 'Prospect'))}",
                f"**Channel:** {humanize_key(item.get('channel', 'Not specified'))}",
                f"**Qualification:** {humanize_key(item.get('qualification', 'Review'))}",
                _safe_report_text(item.get("reason", "")),
                "",
                _safe_report_text(item.get("message", "")),
                "",
                f"**Follow-up target:** {item.get('follow_up_days', 5)} days",
                "Approval: Required before any external sending.",
                "",
            ])
        note = parse_model_note(data.get("qualification_note", ""))
        if note:
            lines.extend(["## Qualification Notes", _safe_report_text(note), ""])
        policy = data.get("policy") or "No message is sent automatically. An authorized reviewer must approve each action."
        lines.extend(["## External Action Policy", _safe_report_text(policy)])

    elif workflow == "kpi":
        updates = _list_of_mappings(data.get("normalized_updates"))
        risks = _list_of_mappings(data.get("risks"))
        lines.extend([
            "## Executive Snapshot",
            _safe_report_text(data.get("leadership_brief", "Progress was summarized from the submitted updates.")),
            "",
            f"- Updates reviewed: {len(updates)}",
            f"- Risks requiring attention: {int(data.get('risk_count', len(risks)) or 0)}",
            "",
            "## Performance Updates",
            "",
        ])
        for update in updates:
            lines.append(
                f"- **{_safe_report_text(update.get('entity', 'Entity'))} — {_safe_report_text(update.get('metric', 'Metric'))}:** "
                f"{_safe_report_text(update.get('value', 'Not reported'))} "
                f"(Status: {humanize_key(update.get('status', 'unknown'))}; Blocker: {_safe_report_text(update.get('blocker', 'None reported'))})"
            )
        lines.extend(["", "## Risks", ""])
        if risks:
            for risk in risks:
                lines.append(
                    f"- **{_safe_report_text(risk.get('entity', 'Entity'))} — {_safe_report_text(risk.get('metric', 'Metric'))}:** "
                    f"{_safe_report_text(risk.get('blocker', 'Risk reported'))}"
                )
        else:
            lines.append("No blockers were reported in the submitted updates.")
        note = parse_model_note(data.get("executive_note", ""))
        if note:
            lines.extend(["", "## Executive Commentary", _safe_report_text(note), ""])
        lines.append("_Validate source data and ownership before using this briefing for executive decisions._")

    else:
        lines.extend(["## Summary", _safe_report_text(data.get("summary") or data.get("result") or "Execution completed."), ""])
        for key, value in data.items():
            if key in {"summary", "result"} or value in (None, "", [], {}):
                continue
            lines.extend([f"## {humanize_key(key)}", _safe_report_text(value), ""])

    return "\n".join(lines).strip() + "\n"
