import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.integrations.llm import LLMProvider
from app.storage.repository import Repository
from app.workflows.orchestrator import WorkflowEngine

logger = logging.getLogger(__name__)


def execute_run(run_id: str, workflow: str, payload: dict) -> dict:
    settings = get_settings()
    repo = Repository(settings)
    repo.mark_running(run_id)
    try:
        result = WorkflowEngine(LLMProvider(settings)).run(workflow, payload, run_id=run_id)
        result["run_id"] = run_id
        repo.save_result(result)
        return result
    except Exception as exc:
        logger.exception("worker_execution_failed run_id=%s", run_id)
        result = {
            "run_id": run_id,
            "workflow": workflow,
            "status": "failed",
            "output": {"error": str(exc), "recovery": "Inspect worker logs and retry the run."},
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(timezone.utc),
            "usage": None,
            "warnings": ["Worker failed before workflow completion."],
        }
        repo.save_result(result)
        raise
