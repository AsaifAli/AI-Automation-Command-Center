
import logging
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from redis import Redis
from rq import Queue
from starlette.responses import Response

from app.core.auth import Principal, authenticate_api_key, require_permission
from app.core.config import get_settings
from app.core.llm_gateway_context import (
    reset_llm_gateway_token,
    set_llm_gateway_token,
)
from app.core.logging import configure_logging
from app.core.telemetry import configure_telemetry
from app.integrations.llm import LLMProvider
from app.models.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    HealthResponse,
    MetricResponse,
    RunListResponse,
    RunRequest,
    WorkflowResult,
)
from app.storage.repository import Repository
from app.workers.jobs import execute_run

settings = get_settings()
configure_logging(settings.log_level)

logger = logging.getLogger(__name__)
repo = Repository(settings)

PROM_METRICS = {
    name: Gauge(name, f"{name.replace('_', ' ')}")
    for name in (
        "workflow_runs_total",
        "workflow_runs_completed",
        "workflow_runs_failed",
        "workflow_runs_queued",
        "workflow_runs_running",
        "workflow_approvals_total",
        "workflow_approvals_approved",
    )
}

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "AI automation control plane with asynchronous execution, "
        "role-based authorization, human approval and observability."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.cors_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-API-Key",
        "X-Request-ID",
        "X-LLM-Gateway-Token",
    ],
)


def queue() -> Queue:
    return Queue(
        settings.queue_name,
        connection=Redis.from_url(settings.redis_url),
    )


@app.on_event("startup")
def startup() -> None:
    if settings.database_url.strip():
        repo.init_db()
    else:
        logger.info(
            "database persistence disabled; using ephemeral in-memory state"
        )

    LLMProvider(settings)
    configure_telemetry(settings)
    logger.info("api_started version=%s", settings.app_version)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get(
        "X-Request-ID",
        str(uuid.uuid4()),
    )

    content_length = request.headers.get("content-length")

    if content_length:
        try:
            if int(content_length) > settings.max_payload_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request payload too large"},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
            )

    context_token = set_llm_gateway_token(
        request.headers.get("X-LLM-Gateway-Token", "")
    )

    started = time.perf_counter()

    try:
        response = await call_next(request)
    finally:
        reset_llm_gateway_token(context_token)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"

    logger.info(
        "request method=%s path=%s status=%s latency_ms=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        int((time.perf_counter() - started) * 1000),
        request_id,
    )

    return response


@app.get("/", tags=["system"])
def root():
    state_store = (
        "ephemeral-memory"
        if not settings.database_url.strip()
        else "postgresql"
    )

    architecture = (
        "api -> inline execution -> ephemeral in-memory state"
        if settings.inline_execution and not settings.database_url.strip()
        else "api -> execution -> persistent postgres state"
        if settings.database_url.strip()
        else "api -> execution -> ephemeral in-memory state"
    )

    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "architecture": architecture,
        "state_store": state_store,
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
def health():
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        demo_mode=settings.demo_mode,
    )


@app.get("/ready", tags=["system"])
def ready():
    # Kept public for Docker and deployment health probes.
    try:
        repo.metrics()

        redis_status = (
            "inline" if settings.inline_execution else "available"
        )

        if not settings.inline_execution:
            Redis.from_url(settings.redis_url).ping()

        return {
            "status": "ready",
            "state_store": (
                "ephemeral-memory"
                if not settings.database_url.strip()
                else "postgresql"
            ),
            "redis": redis_status,
            "llm": "demo" if settings.demo_mode else "configured",
        }

    except Exception:
        logger.exception("readiness_check_failed")

        raise HTTPException(
            status_code=503,
            detail="Readiness check failed",
        ) from None


@app.get(
    "/metrics",
    response_model=MetricResponse,
    tags=["observability"],
)
def metrics(
    _: Principal = Depends(
        require_permission("metrics:read")
    ),
):
    return repo.metrics()


@app.get(
    "/prometheus",
    include_in_schema=False,
)
def prometheus_metrics(authorization: str | None = Header(default=None, alias="Authorization")):
    settings = get_settings()
    if settings.api_auth_enabled:
        scheme, separator, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not separator or not token:
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
        principal = authenticate_api_key(token, settings)
        if not principal.can("metrics:read"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    values = repo.metrics()
    for name, gauge in PROM_METRICS.items():
        gauge.set(values.get(name, 0))
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get(
    "/api/v1/workflows",
    tags=["workflows"],
)
def workflows(
    _: Principal = Depends(
        require_permission("workflows:read")
    ),
):
    return {
        "workflows": [
            {
                "name": "content",
                "purpose": "Platform-ready content drafts with review gates",
            },
            {
                "name": "competitor",
                "purpose": "Evidence-oriented competitor intelligence",
            },
            {
                "name": "outreach",
                "purpose": "Partner qualification and approval-gated drafts",
            },
            {
                "name": "kpi",
                "purpose": "Normalized KPI and leadership briefing",
            },
        ]
    }


@app.post(
    "/api/v1/runs",
    response_model=WorkflowResult,
    status_code=202,
    tags=["workflows"],
)
def run_workflow(
    request: RunRequest,
    llm_gateway_token: str = Header(
        default="",
        alias="X-LLM-Gateway-Token",
    ),
    _: Principal = Depends(
        require_permission("runs:execute")
    ),
):
    # Never serialize a request-scoped token into Redis/RQ job data.
    if llm_gateway_token and not settings.inline_execution:
        raise HTTPException(
            status_code=400,
            detail=(
                "Request-scoped gateway tokens are supported only "
                "with inline execution. Use configured service-level "
                "LLM credentials for queued runs."
            ),
        )

    run_id = repo.create_pending_run(
        request.workflow.value
    )

    payload = dict(request.payload)

    if settings.inline_execution:
        execute_run(
            run_id,
            request.workflow.value,
            payload,
            use_request_context=True,
        )
    else:
        queue().enqueue(
            execute_run,
            run_id,
            request.workflow.value,
            payload,
            job_id=run_id,
        )

    result = repo.get_run(run_id)

    if not result:
        raise HTTPException(
            status_code=500,
            detail="Failed to create workflow run",
        )

    return result


@app.get(
    "/api/v1/runs",
    response_model=RunListResponse,
    tags=["workflows"],
)
def list_runs(
    limit: int = 20,
    _: Principal = Depends(
        require_permission("runs:read")
    ),
):
    safe_limit = min(max(limit, 1), 100)

    try:
        runs = repo.list_runs(safe_limit)

        return {
            "runs": runs,
            "total": len(runs),
        }

    except Exception:
        logger.exception("run_history_failed")

        raise HTTPException(
            status_code=503,
            detail="Run history unavailable",
        ) from None


@app.delete(
    "/api/v1/runs/history",
    tags=["workflows"],
)
def clear_run_history(
    _: Principal = Depends(
        require_permission("runs:delete")
    ),
):
    try:
        deleted = repo.clear_history()

        return {
            "deleted_runs": deleted,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except Exception:
        logger.exception("run_history_clear_failed")

        raise HTTPException(
            status_code=503,
            detail="Unable to clear run history",
        ) from None


@app.get(
    "/api/v1/runs/{run_id}",
    response_model=WorkflowResult,
    tags=["workflows"],
)
def get_run(
    run_id: str,
    _: Principal = Depends(
        require_permission("runs:read")
    ),
):
    try:
        result = repo.get_run(run_id)

    except Exception:
        logger.exception(
            "run_lookup_failed run_id=%s",
            run_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Run lookup unavailable",
        ) from None

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Run not found",
        )

    return result


@app.post(
    "/api/v1/runs/{run_id}/approval",
    response_model=ApprovalResponse,
    tags=["approvals"],
)
def decide_approval(
    run_id: str,
    request: ApprovalRequest,
    principal: Principal = Depends(
        require_permission("approvals:decide")
    ),
):
    result = repo.get_run(run_id)

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Run not found",
        )

    if result["workflow"] != "outreach":
        raise HTTPException(
            status_code=400,
            detail="Only outreach runs support human approval",
        )

    # The authenticated principal determines reviewer identity.
    # The legacy request.reviewer field is deliberately ignored.
    return repo.create_approval(
        run_id,
        request.decision,
        principal.subject,
        request.note,
    )
