from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
HARNESS_HOST = "ai-engineering-evaluation-harness.onrender.com"


def build_flowpilot_event(result: dict[str, Any]) -> dict[str, Any] | None:
    """Create a privacy-bounded activity event; never copy workflow content."""
    run_id = result.get("run_id")
    workflow = result.get("workflow")
    if not isinstance(run_id, str) or not 8 <= len(run_id) <= 64:
        return None
    if workflow not in {"content", "competitor", "outreach", "kpi"}:
        return None

    raw_status = result.get("status")
    if raw_status == "failed":
        status = "failed"
        event_type = "run.failed"
    elif raw_status == "completed_with_warnings":
        status = "warning"
        event_type = "run.completed"
    else:
        # awaiting_approval is a completed, successful preparation step. The
        # actual external-impact action remains blocked pending human review.
        status = "success"
        event_type = "run.completed"

    output = result.get("output")
    output = output if isinstance(output, dict) else {}
    metrics: dict[str, int | float | bool] = {
        "warning_count": len(result.get("warnings") or []),
    }

    if workflow == "content":
        posts = output.get("posts")
        posts = posts if isinstance(posts, list) else []
        approval = output.get("approval")
        metrics["draft_count"] = len(posts)
        metrics["approval_required"] = bool(
            isinstance(approval, dict) and approval.get("status") == "pending"
        )
    elif workflow == "competitor":
        competitors = output.get("competitors")
        competitors = competitors if isinstance(competitors, list) else []
        actions = output.get("recommended_actions")
        metrics["competitor_count"] = len(competitors)
        metrics["source_count"] = max(0, int(output.get("source_count") or 0))
        metrics["recommendation_count"] = len(actions) if isinstance(actions, list) else 0
    elif workflow == "outreach":
        candidates = output.get("candidates")
        candidates = candidates if isinstance(candidates, list) else []
        approval = output.get("approval")
        guarded = sum(
            1 for item in candidates
            if isinstance(item, dict) and item.get("approval_required") is True
        )
        metrics["candidate_count"] = len(candidates)
        metrics["approval_guarded_count"] = guarded
        metrics["approval_required"] = bool(
            isinstance(approval, dict) and approval.get("status") == "pending"
        )
    elif workflow == "kpi":
        risks = output.get("risks")
        updates = output.get("normalized_updates")
        metrics["risk_count"] = len(risks) if isinstance(risks, list) else 0
        metrics["update_count"] = len(updates) if isinstance(updates, list) else 0

    usage = result.get("usage")
    model = usage.get("model") if isinstance(usage, dict) else None
    event: dict[str, Any] = {
        "event_id": run_id,
        "project": "flowpilot",
        "event_type": event_type,
        "status": status,
        "workflow": workflow,
        "duration_ms": max(0, min(900_000, int(result.get("duration_ms") or 0))),
        "request_id": run_id,
        "metrics": metrics,
    }
    if isinstance(model, str) and model.strip():
        event["model"] = model.strip()[:120]
    return event


def _send_event(url: str, token: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            if response.status not in {200, 202}:
                logger.warning("evaluation_harness_event_rejected status=%s", response.status)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # Telemetry is best-effort and must never interrupt a FlowPilot run.
        logger.warning("evaluation_harness_event_delivery_failed")


def publish_flowpilot_event(
    result: dict[str, Any],
    settings: Any,
    *,
    thread_factory: Any = threading.Thread,
) -> bool:
    """Dispatch bounded telemetry on a daemon thread; disabled by default."""
    payload = build_flowpilot_event(result)
    if payload is None:
        return False
    base_url = str(getattr(settings, "evaluation_harness_url", "") or "").strip()
    token = str(getattr(settings, "evaluation_harness_ingest_token", "") or "").strip()
    if not base_url or not token:
        return False

    parsed = urlsplit(base_url)
    is_production = str(getattr(settings, "app_env", "")).strip().lower() in {"prod", "production"}
    production_origin = (
        parsed.scheme == "https"
        and parsed.hostname == HARNESS_HOST
        and parsed.port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )
    local_origin = (
        not is_production
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and parsed.port is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )
    if not (production_origin or local_origin):
        logger.warning("evaluation_harness_event_delivery_skipped_invalid_origin")
        return False

    url = base_url.rstrip("/") + "/api/application-events"
    worker = thread_factory(
        target=_send_event,
        args=(url, token, payload),
        name="flowpilot-evaluation-telemetry",
        daemon=True,
    )
    worker.start()
    return True


def publish_flowpilot_evaluation_event(
    summary: dict[str, Any],
    settings: Any,
    *,
    thread_factory: Any = threading.Thread,
) -> bool:
    """Report a completed deterministic evaluation without transmitting case data."""
    run_id = summary.get("run_id")
    cases = summary.get("cases")
    passed = summary.get("passed")
    duration_ms = summary.get("duration_ms")
    if (
        not isinstance(run_id, str)
        or not 8 <= len(run_id) <= 64
        or not isinstance(cases, int)
        or isinstance(cases, bool)
        or cases < 1
        or not isinstance(passed, int)
        or isinstance(passed, bool)
        or passed < 0
        or passed > cases
    ):
        return False

    base_url = str(getattr(settings, "evaluation_harness_url", "") or "").strip()
    token = str(getattr(settings, "evaluation_harness_ingest_token", "") or "").strip()
    if not base_url or not token:
        return False

    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        return False

    is_production = str(getattr(settings, "app_env", "")).strip().lower() in {"prod", "production"}
    production_origin = (
        parsed.scheme == "https"
        and parsed.hostname == HARNESS_HOST
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )
    local_origin = (
        not is_production
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and port is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )
    if not (production_origin or local_origin):
        logger.warning("evaluation_harness_evaluation_delivery_skipped_invalid_origin")
        return False

    safe_duration = 0
    try:
        safe_duration = max(0, min(900_000, int(duration_ms or 0)))
    except (TypeError, ValueError, OverflowError):
        pass

    payload = {
        "event_id": run_id,
        "project": "flowpilot",
        "event_type": "evaluation.completed",
        "status": "success" if passed == cases else "warning",
        "workflow": "deterministic_replay",
        "duration_ms": safe_duration,
        "request_id": run_id,
        "metrics": {
            "cases": cases,
            "passed": passed,
            "pass_rate": round(passed / cases, 6),
        },
    }
    url = base_url.rstrip("/") + "/api/application-events"
    worker = thread_factory(
        target=_send_event,
        args=(url, token, payload),
        name="flowpilot-evaluation-telemetry",
        daemon=True,
    )
    worker.start()
    return True
