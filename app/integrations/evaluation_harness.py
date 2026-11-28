from __future__ import annotations

import hashlib
import logging
import math
import os
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



def build_flowpilot_evaluation_event(
    result: dict[str, Any],
    *,
    completed_steps: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build scalar runtime quality signals for real FlowPilot stage outcomes.

    This is deterministic workflow/approval evaluation, not an LLM judge. The
    payload excludes workflow input, generated output, evidence details, prompts
    and agent traces. Tool-use metrics are intentionally omitted because the
    current FlowPilot runtime executes bounded workflow agents rather than a
    dynamic tool-calling agent trace.
    """
    run_id = str(result.get("run_id", "")).strip()
    if len(run_id) < 8 or len(run_id) > 256:
        raise ValueError("A bounded workflow run ID is required for evaluation.")

    workflow = str(result.get("workflow", "unknown")).strip()[:80] or "unknown"
    status = str(result.get("status", "failed")).strip().lower()
    workflow_completed = status in {
        "completed",
        "completed_with_warnings",
        "awaiting_approval",
    }

    actual_steps = {
        step for step in completed_steps
        if isinstance(step, str) and step in {"execute", "validate"}
    }
    required_steps = {"execute", "validate"}
    step_coverage = len(actual_steps & required_steps) / len(required_steps)

    output = result.get("output") if isinstance(result.get("output"), dict) else {}
    approval = output.get("approval")
    approval_state = (
        str(approval.get("status", "")).strip().lower()
        if isinstance(approval, dict)
        else ""
    )
    approval_required = bool(
        isinstance(approval, dict)
        or (
            workflow == "content" and output.get("posts")
        )
        or (
            workflow == "outreach" and output.get("candidates")
        )
    )
    if not approval_required:
        approval_compliance = 1.0
    else:
        approval_compliance = float(
            approval_state in {"pending", "awaiting_approval", "approved", "rejected"}
        )
    if output.get("external_action_executed") is True and approval_state != "approved":
        approval_compliance = 0.0

    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    has_audit_trace = any(
        isinstance(item, dict) and item.get("source") == "workflow-engine"
        for item in evidence
    )
    audit_traceability = float(bool(run_id) and has_audit_trace)

    metrics: dict[str, float | int] = {
        "runtime.workflow_completion": float(workflow_completed),
        "runtime.step_coverage": round(step_coverage, 6),
        "runtime.validation_completed": float("validate" in actual_steps),
        "runtime.approval_compliance": approval_compliance,
        "runtime.approval_pending": float(
            status == "awaiting_approval"
            or approval_state in {"pending", "awaiting_approval"}
        ),
        "runtime.audit_traceability": audit_traceability,
        "runtime.warning_count": min(
            len(result.get("warnings", []))
            if isinstance(result.get("warnings"), list)
            else 0,
            2_147_483_647,
        ),
    }

    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    provider = usage.get("provider")
    model = usage.get("model")
    model_label = "/".join(
        str(value).strip()[:55]
        for value in (provider, model)
        if isinstance(value, str) and value.strip()
    )[:120] or None

    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
    event: dict[str, Any] = {
        "event_id": "eval-" + digest,
        "project": "flowpilot",
        "event_type": "evaluation.completed",
        "status": "success",
        "workflow": workflow,
        "request_id": run_id[:100],
        "metrics": metrics,
    }
    if model_label:
        event["model"] = model_label
        event["model_version"] = str(model or model_label)[:128]

    for field, env_name in (
        ("experiment_id", "EVALUATION_EXPERIMENT_ID"),
        ("dataset_version", "EVALUATION_DATASET_VERSION"),
        ("prompt_version", "EVALUATION_PROMPT_VERSION"),
        ("config_version", "EVALUATION_CONFIG_VERSION"),
    ):
        value = os.getenv(env_name, "").strip()
        if value:
            event[field] = value[:128]

    return event


def report_workflow_evaluation_event(
    settings: Any,
    result: dict[str, Any],
    *,
    completed_steps: list[str] | tuple[str, ...] = (),
) -> bool:
    """Post workflow evaluation scores; telemetry failure never affects a run."""
    endpoint = _trusted_harness_endpoint(
        str(getattr(settings, "evaluation_harness_url", "")),
        str(getattr(settings, "app_env", "development")),
    )
    token = str(getattr(settings, "evaluation_harness_ingest_token", "")).strip()
    if not endpoint or not token or len(token) > 512:
        return False

    try:
        payload = build_flowpilot_evaluation_event(
            result,
            completed_steps=completed_steps,
        )
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json=payload,
            timeout=1.5,
        )
        if response.status_code != 202:
            logger.info(
                "evaluation_harness_score_not_accepted workflow=%s status=%s",
                payload["workflow"],
                response.status_code,
            )
            return False
        return True
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        logger.info("evaluation_harness_score_delivery_failed", exc_info=True)
        return False
    except Exception:  # noqa: BLE001 - an evaluator outage must never affect workflow execution.
        logger.info("evaluation_harness_score_delivery_failed", exc_info=True)
        return False
