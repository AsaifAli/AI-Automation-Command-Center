import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.gateway_credentials import GatewayCredentialError, GatewayCredentialVault
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
    gateway_credential_id: str | None = None,
) -> dict:
    settings = get_settings()
    repo = Repository(settings)
    context_token = None

    try:
        if gateway_credential_id:
            encrypted_token = repo.consume_gateway_job_credential(
                run_id,
                gateway_credential_id,
            )
            if encrypted_token is None:
                raise GatewayCredentialError(
                    "The queued Gateway session is missing, expired, or already consumed."
                )
            vault = GatewayCredentialVault(
                settings.gateway_job_credential_encryption_key,
                settings.gateway_job_credential_ttl_seconds,
            )
            bearer_token = vault.decrypt(encrypted_token)
            context_token = set_llm_gateway_token(bearer_token)
        elif not use_request_context:
            # A worker must never inherit a token from a previous job/thread.
            context_token = set_llm_gateway_token("")

        repo.mark_running(run_id)
        safe_payload = dict(payload)

        # Remove legacy tokens from jobs queued by older application versions.
        legacy_token = safe_payload.pop("_llm_gateway_token", None)
        if legacy_token:
            logger.warning("legacy_gateway_token_ignored run_id=%s", run_id)

        result = WorkflowEngine(LLMProvider(settings)).run(
            workflow,
            safe_payload,
            run_id=run_id,
        )
        result["run_id"] = run_id
        repo.save_result(result)
        return result

    except Exception:
        logger.exception("worker_execution_failed run_id=%s", run_id)
        result = {
            "run_id": run_id,
            "workflow": workflow,
            "status": "failed",
            "output": {
                "error": "Workflow execution failed.",
                "recovery": "Inspect worker logs and retry after correcting the input or Gateway session.",
            },
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": ["Worker failed before workflow completion."],
        }
        repo.save_result(result)
        raise
    finally:
        if context_token is not None:
            reset_llm_gateway_token(context_token)
