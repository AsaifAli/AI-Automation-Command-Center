from __future__ import annotations

import logging
import math
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value < 0:
        return None
    return min(int(value), 2_147_483_647)


def build_flowpilot_event(result: dict[str, Any]) -> dict[str, Any]:
    """Create the telemetry contract without workflow prompts, output or evidence."""
    status = str(result.get("status", "failed"))
    if status == "failed":
        event_status = "failed"
    elif status in {"awaiting_approval", "completed_with_warnings"}:
        event_status = "warning"
    else:
        event_status = "success"

    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    output = result.get("output") if isinstance(result.get("output"), dict) else {}
    approval = output.get("approval")
    approval_state = str(approval.get("status", "")).strip().lower() if isinstance(approval, dict) else ""
    approval_pending = status == "awaiting_approval" or approval_state in {"pending", "awaiting_approval"}

    metrics: dict[str, float | int | bool] = {
        "warning_count": len(result.get("warnings", [])) if isinstance(result.get("warnings"), list) else 0,
        "approval_pending": approval_pending,
    }
    for source, target in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = _safe_int(usage.get(source))
        if value is not None:
            metrics[target] = value

    cost_available = usage.get("cost_estimate_available")
    if isinstance(cost_available, bool):
        metrics["cost_estimate_available"] = cost_available

    provider = usage.get("provider")
    model = usage.get("model")
    model_label = "/".join(
        str(value).strip()[:55]
        for value in (provider, model)
        if isinstance(value, str) and value.strip()
    )[:120] or None

    run_id = str(result.get("run_id", "")).strip()
    if len(run_id) < 8 or len(run_id) > 64:
        raise ValueError("A bounded workflow run ID is required for telemetry.")

    workflow = str(result.get("workflow", "unknown")).strip()[:80] or "unknown"
    duration_ms = _safe_int(result.get("duration_ms"))

    event: dict[str, Any] = {
        "event_id": run_id,
        "project": "flowpilot",
        "event_type": "run.failed" if event_status == "failed" else "run.completed",
        "status": event_status,
        "workflow": workflow,
        "request_id": run_id[:100],
        "metrics": metrics,
    }
    if duration_ms is not None:
        event["duration_ms"] = min(duration_ms, 900_000)
    if model_label:
        event["model"] = model_label
    return event


def _trusted_harness_endpoint(configured_url: str, app_env: str) -> str | None:
    candidate = (configured_url or "").strip()
    if not candidate or len(candidate) > 500:
        return None
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower()
        local_http = (
            app_env.strip().lower() not in {"prod", "production"}
            and parsed.scheme == "http"
            and host in {"localhost", "127.0.0.1"}
        )
        if (
            (parsed.scheme != "https" and not local_http)
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 80, 443, 8000)
        ):
            return None
        return candidate.rstrip("/") + "/api/application-events"
    except ValueError:
        return None


def report_workflow_event(settings: Any, result: dict[str, Any]) -> bool:
    """Post bounded FlowPilot run metadata; never let telemetry affect a workflow."""
    endpoint = _trusted_harness_endpoint(
        str(getattr(settings, "evaluation_harness_url", "")),
        str(getattr(settings, "app_env", "development")),
    )
    token = str(getattr(settings, "evaluation_harness_ingest_token", "")).strip()
    if not endpoint or not token or len(token) > 512:
        return False

    try:
        payload = build_flowpilot_event(result)
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json=payload,
            timeout=1.5,
        )
        if response.status_code != 202:
            logger.info(
                "evaluation_harness_event_not_accepted status=%s workflow=%s",
                response.status_code,
                payload["workflow"],
            )
            return False
        return True
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        logger.info("evaluation_harness_event_delivery_failed", exc_info=True)
        return False
