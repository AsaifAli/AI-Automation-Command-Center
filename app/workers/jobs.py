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


def record_rq_job_failure(job, connection, exc_type, exc_value, traceback) -> None:
    """Persist failures that bypass execute_run, including hard RQ job timeouts."""
    args = getattr(job, "args", None) or ()
    if len(args) < 2:
        logger.error("rq_job_failure_missing_run_reference job_id=%s", getattr(job, "id", "unknown"))
        return
    run_id, workflow = args[0], args[1]
    if not isinstance(run_id, str) or not isinstance(workflow, str):
        logger.error("rq_job_failure_invalid_run_reference job_id=%s", getattr(job, "id", "unknown"))
        return
    settings = get_settings()
    repo = Repository(settings)
    failure = {
        "run_id": run_id,
        "workflow": workflow,
        "status": "failed",
        "output": {
            "error": "The background worker could not complete this workflow.",
            "failure_code": "worker_job_failed",
            "recovery": "Review worker logs and submit a new run after correcting the issue.",
        },
        "evidence": [],
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": ["The RQ worker reported a terminal job failure or timeout."],
    }
    repo.fail_run_if_active(failure)


def record_rq_job_stopped(job, connection) -> None:
    """Make an externally stopped RQ job terminal unless the run is already stopped."""
    args = getattr(job, "args", None) or ()
    if not args or not isinstance(args[0], str):
        return
    Repository(get_settings()).stop_run(args[0])


def execute_run(
    run_id: str,
    workflow: str,
    payload: dict,
    *,
    use_request_context: bool = False,
    gateway_credential_id: str | None = None,
    encrypted_gateway_token: bytes | None = None,
    gateway_token_ttl_seconds: int | None = None,
    rq_generation: int | None = None,
    rq_job_id: str | None = None,
    persist_success: bool = True,
    persist_failure: bool = True,
) -> dict:
    settings = get_settings()
    repo = Repository(settings)
    context_token = None

    try:
        # The repository is the concurrency gate. A queued job can be delivered
        # just as an operator pauses/stops it; do not consume one-time credentials
        # or call the model unless the run is still eligible to start.
        if persist_success:
            claimed = (
                repo.claim_rq_run(run_id, rq_generation, rq_job_id)
                if rq_job_id is not None
                else repo.mark_running(run_id)
            )
        else:
            # The signed QStash HTTP handler has already claimed and checked
            # this run before entering the shared execution function.
            claimed = True

        if not claimed:
            current = repo.get_run(run_id)
            return {
                "run_id": run_id,
                "workflow": workflow,
                "status": (current or {}).get("status", "missing"),
                "output": (current or {}).get("output", {}),
                "evidence": (current or {}).get("evidence", []),
                "duration_ms": (current or {}).get("duration_ms", 0),
                "created_at": (current or {}).get("created_at", datetime.now(UTC)),
                "usage": (current or {}).get("usage"),
                "warnings": (current or {}).get("warnings", []),
            }

        if gateway_credential_id and encrypted_gateway_token is not None:
            raise ValueError("Provide either a Gateway credential reference or encrypted token, not both.")
        if encrypted_gateway_token is not None:
            vault = GatewayCredentialVault(
                settings.gateway_job_credential_encryption_key,
                gateway_token_ttl_seconds or settings.gateway_job_credential_ttl_seconds,
            )
            context_token = set_llm_gateway_token(vault.decrypt(encrypted_gateway_token))
        elif gateway_credential_id:
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
        if persist_success and not repo.save_paused_result(run_id, result):
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
        if persist_failure and not repo.save_paused_result(run_id, result):
            repo.save_result(result)
        raise
    finally:
        if context_token is not None:
            reset_llm_gateway_token(context_token)
