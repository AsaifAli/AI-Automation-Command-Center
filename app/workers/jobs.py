
import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.llm_gateway_context import (
    reset_llm_gateway_token,
    set_llm_gateway_token,
)
from app.integrations.llm import LLMProvider
from app.storage.repository import Repository
from app.workflows.orchestrator import WorkflowEngine

logger = logging.getLogger(__name__)


def execute_run(
    run_id: str,
    workflow: str,
    payload: dict,
    *,
    use_request_context: bool = False,
) -> dict:
    settings = get_settings()
    repo = Repository(settings)

    repo.mark_running(run_id)

    context_token = None

    if not use_request_context:
        # Background workers must not inherit caller credentials.
        context_token = set_llm_gateway_token("")

    payload = dict(payload)

    # Remove legacy tokens from jobs queued by older application versions.
    legacy_token = payload.pop("_llm_gateway_token", None)

    if legacy_token:
        logger.warning(
            "legacy_gateway_token_ignored run_id=%s",
            run_id,
        )

    try:
        result = WorkflowEngine(
            LLMProvider(settings)
        ).run(
            workflow,
            payload,
            run_id=run_id,
        )

        result["run_id"] = run_id
        repo.save_result(result)

        return result

    except Exception:
        logger.exception(
            "worker_execution_failed run_id=%s",
            run_id,
        )

        result = {
            "run_id": run_id,
            "workflow": workflow,
            "status": "failed",
            "output": {
                "error": "Workflow execution failed.",
                "recovery": (
                    "Inspect worker logs and retry the run."
                ),
            },
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(timezone.utc),
            "usage": None,
            "warnings": [
                "Worker failed before workflow completion."
            ],
        }

        repo.save_result(result)
        raise

    finally:
        if context_token is not None:
            reset_llm_gateway_token(context_token)
