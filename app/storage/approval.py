"""Shared approval-state transitions for workflow runs."""

from typing import Any

_EXECUTION_NOTE = (
    "Review decisions are recorded. No external action was performed because "
    "no publishing or sending connector is configured."
)


def apply_approval_decision(
    run: dict[str, Any],
    approval_record: dict[str, Any],
    *,
    item_index: int | None = None,
) -> None:
    """Apply one reviewer decision to a workflow or an individual content draft."""
    if run.get("status") != "awaiting_approval":
        raise ValueError("This run is no longer awaiting approval")

    output = run.setdefault("output", {})
    request = output.get("approval")
    if not isinstance(request, dict) or request.get("status") != "pending":
        raise ValueError("No pending approval exists for this run")

    decision = approval_record["decision"]
    if run.get("workflow") == "content":
        if item_index is None:
            raise ValueError("Content drafts must be approved individually with an item_index")
        items = request.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("No content drafts are available for individual approval")
        if item_index < 0 or item_index >= len(items):
            raise ValueError("Content draft item_index is out of range")

        item = items[item_index]
        if not isinstance(item, dict):
            raise ValueError("Content draft is invalid")
        if item.get("approval_status", "pending") != "pending":
            raise ValueError("This content draft has already been reviewed")

        decided_at = approval_record["decided_at"]
        item.update(
            {
                "approval_status": decision,
                "reviewer": approval_record["reviewer"],
                "note": approval_record["note"],
                "decided_at": decided_at.isoformat(),
                "approval_id": approval_record["approval_id"],
            }
        )
        posts = output.get("posts")
        if isinstance(posts, list) and item_index < len(posts) and isinstance(posts[item_index], dict):
            posts[item_index].update(
                {
                    "approval_status": decision,
                    "approval_reviewer": approval_record["reviewer"],
                    "approval_note": approval_record["note"],
                    "approval_decided_at": decided_at.isoformat(),
                }
            )

        statuses = [
            draft.get("approval_status", "pending") if isinstance(draft, dict) else "pending"
            for draft in items
        ]
        approved_count = statuses.count("approved")
        rejected_count = statuses.count("rejected")
        pending_count = len(statuses) - approved_count - rejected_count
        request.update(
            {
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "pending_count": pending_count,
                "total_count": len(statuses),
            }
        )
        approval_record["item_index"] = item_index

        if pending_count:
            # Partial decisions stay in the inbox and do not complete the run.
            run["status"] = "awaiting_approval"
            return

        final_decision = "approved" if rejected_count == 0 else "rejected"
        request.update(
            {
                "status": final_decision,
                "decision": final_decision,
                "decided_at": decided_at.isoformat(),
                "execution_status": "not_configured",
                "execution_note": _EXECUTION_NOTE,
            }
        )
        run["status"] = (
            "completed_with_warnings"
            if final_decision == "approved" and run.get("warnings")
            else "completed" if final_decision == "approved" else "rejected"
        )
        return

    if item_index is not None:
        raise ValueError("item_index is only supported for content drafts")

    next_status = decision
    decided_at = approval_record["decided_at"]
    request.update(
        {
            "status": next_status,
            "decision": decision,
            "reviewer": approval_record["reviewer"],
            "note": approval_record["note"],
            "decided_at": decided_at.isoformat(),
            "execution_status": "not_configured",
            "execution_note": _EXECUTION_NOTE,
        }
    )
    run["status"] = next_status
