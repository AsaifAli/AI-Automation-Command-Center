
import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from redis import Redis
from rq import Queue
from starlette.responses import Response

from app.core.auth import Principal, authenticate_api_key, require_permission
from app.core.config import get_settings
from app.core.gateway_credentials import GatewayCredentialError, GatewayCredentialVault
from app.core.llm_gateway_context import (
    reset_llm_gateway_token,
    set_llm_gateway_token,
)
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimitBackendUnavailable, check_request_limits
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
from app.storage.repository import Repository, _json_dumps
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
        "workflow_approvals_pending",
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


class GatewaySessionLifetime(int):
    """Integer-compatible Gateway TTL carrying its verified opaque owner scope."""

    def __new__(cls, remaining_seconds: int, owner_scope: str):
        instance = int.__new__(cls, remaining_seconds)
        instance.owner_scope = owner_scope
        return instance


def verify_flowpilot_gateway_session(gateway_token: str) -> GatewaySessionLifetime:
    """Validate a Gateway bearer and require the FlowPilot project audience.

    Returns remaining Gateway-session lifetime in seconds. The token is sent
    only to the Gateway status endpoint and is never logged or persisted here.
    """
    gateway_base = (settings.llm_gateway_url or settings.llm_base_url).strip()
    if not gateway_base or any(char.isspace() for char in gateway_base):
        raise HTTPException(
            status_code=503,
            detail="Gateway BYOK is not configured on the workflow service.",
        )

    try:
        parsed_gateway = urlsplit(gateway_base)
        gateway_host = (parsed_gateway.hostname or "").rstrip(".").lower()
        gateway_port = parsed_gateway.port
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="The configured Gateway URL is invalid.",
        ) from None

    approved_hosts = {
        item.strip().rstrip(".").lower()
        for item in str(settings.llm_gateway_allowed_hosts or "").split(",")
        if item.strip()
    }
    try:
        gateway_ip = ipaddress.ip_address(gateway_host)
    except ValueError:
        gateway_ip = None
    if gateway_ip is not None and not gateway_ip.is_global:
        raise HTTPException(
            status_code=503,
            detail="The configured Gateway destination is not public.",
        )
    is_production = str(settings.app_env).strip().lower() in {"prod", "production"}
    local_development_host = gateway_host in {"localhost", "127.0.0.1", "::1"}
    valid_scheme = parsed_gateway.scheme == "https" or (
        not is_production
        and local_development_host
        and parsed_gateway.scheme == "http"
    )
    if (
        not valid_scheme
        or not gateway_host
        or (
            gateway_host not in approved_hosts
            and not (not is_production and local_development_host)
        )
        or (gateway_port not in (None, 443) and parsed_gateway.scheme == "https")
        or parsed_gateway.username is not None
        or parsed_gateway.password is not None
        or parsed_gateway.query
        or parsed_gateway.fragment
    ):
        raise HTTPException(
            status_code=503,
            detail="The configured Gateway host is not approved.",
        )

    endpoint = gateway_base.rstrip("/") + "/demo/session/status"
    try:
        with httpx.Client(
            timeout=8.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.get(
                endpoint,
                headers={"Authorization": f"Bearer {gateway_token}"},
            )
    except httpx.HTTPError:
        raise HTTPException(
            status_code=503,
            detail="Gateway session verification is temporarily unavailable.",
        ) from None

    if 300 <= response.status_code < 400:
        raise HTTPException(
            status_code=503,
            detail="Gateway session verification redirects are not allowed.",
        )
    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=401,
            detail="The Gateway session is invalid, expired, or revoked.",
        )
    if response.status_code != 200 or len(response.content) > 16 * 1024:
        raise HTTPException(
            status_code=503,
            detail="Gateway session verification returned an invalid response.",
        )

    try:
        session = response.json()
        active = session.get("active") is True
        correct_project = (
            session.get("project") == "flowpilot"
            and session.get("session_project") == "portfolio"
        )
        expires_at = int(session.get("expires_at"))
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Gateway session verification returned an invalid session.",
        ) from None

    remaining_seconds = expires_at - int(time.time()) - 2
    if not active or not correct_project:
        raise HTTPException(
            status_code=403,
            detail="The Gateway session is not authorized for FlowPilot.",
        )
    if remaining_seconds < 60:
        raise HTTPException(
            status_code=401,
            detail="The Gateway session is expiring. Reopen the portfolio demo and retry.",
        )
    owner_scope = session.get("owner_scope")
    if (
        not isinstance(owner_scope, str)
        or len(owner_scope) != 64
        or any(char not in "0123456789abcdef" for char in owner_scope)
    ):
        # Compatibility for Gateway sessions minted before optional Supabase
        # identity scopes shipped. A session token still receives an isolated
        # scope; a missing claim never falls back to a shared project scope.
        owner_scope = hashlib.sha256(gateway_token.encode("utf-8")).hexdigest()
    return GatewaySessionLifetime(remaining_seconds, owner_scope)


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

    is_write_api = (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.url.path.startswith("/api/v1/")
    )
    if is_write_api:
        client_ip = (
            request.client.host
            if request.client is not None
            else "unknown"
        )
        try:
            allowed, retry_after = await asyncio.to_thread(
                check_request_limits,
                client_ip=client_ip,
                api_key=request.headers.get("X-API-Key", ""),
                settings=settings,
            )
        except RateLimitBackendUnavailable:
            logger.error("write_rate_limit_backend_unavailable request_id=%s", request_id)
            return JSONResponse(
                status_code=503,
                content={"detail": "Write service is temporarily unavailable."},
                headers={"Retry-After": "5"},
            )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Write request limit reached. Retry later."},
                headers={"Retry-After": str(retry_after)},
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

    if settings.qstash_workflow_enabled:
        architecture = (
            "api -> QStash Workflow -> signed execution callback -> "
            + ("PostgreSQL-backed run state" if settings.database_url.strip() else "ephemeral in-memory run state")
        )
    else:
        architecture = (
            "api -> in-process background execution -> ephemeral in-memory state"
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
        execution_mode=(
            "upstash_workflow"
            if settings.qstash_workflow_enabled
            else "inline_background" if settings.inline_execution else "redis_worker"
        ),
    )


@app.get("/ready", tags=["system"])
def ready():
    # Kept public for Docker and deployment health probes.
    try:
        repo.metrics()

        redis_status = (
            "upstash_workflow"
            if settings.qstash_workflow_enabled
            else "inline" if settings.inline_execution else "available"
        )

        if not settings.inline_execution and not settings.qstash_workflow_enabled:
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


def _execute_inline_background(
    run_id: str,
    workflow: str,
    payload: dict,
    gateway_token: str = "",
) -> None:
    """Run a demo workflow after returning its run reference to the UI.

    BYOK bearer tokens are captured only in process memory for this one task,
    installed into the request context for execution, and always cleared.
    They are never written to the run record or logs.
    """
    context_token = set_llm_gateway_token(gateway_token)
    try:
        execute_run(
            run_id,
            workflow,
            payload,
            use_request_context=True,
        )
    except Exception:  # noqa: BLE001 - deliberate isolation boundary for an in-process background task
        # execute_run normally stores a failed result before re-raising. Do not
        # let a post-response task exception surface as a failed HTTP request.
        logger.error("inline_background_task_failed run_id=%s", run_id)
    finally:
        reset_llm_gateway_token(context_token)



def _parse_upstash_workflow_initial_payload(raw_payload: object) -> dict:
    """Normalize SDK initial/failure payloads into the original JSON envelope.

    The Python SDK may hand the callback a decoded mapping, a JSON string, or
    a JSON string nested inside another JSON string. Failure callbacks must
    recover the same envelope that the initial invocation received.
    """
    candidate = raw_payload
    for _ in range(3):
        if isinstance(candidate, dict):
            return candidate
        if isinstance(candidate, bytes):
            try:
                candidate = candidate.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Workflow payload encoding is invalid.") from exc
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise ValueError("Workflow payload is not valid JSON.") from exc
            continue
        break
    raise TypeError("Workflow payload must decode to a JSON object.")


def _validate_upstash_workflow_payload(raw_payload: object) -> dict:
    """Validate the signed QStash envelope before restoring or executing a run."""
    if not isinstance(raw_payload, dict):
        raise TypeError("Workflow payload must be an object.")
    run_id = raw_payload.get("run_id")
    try:
        normalized_run_id = str(uuid.UUID(str(run_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Workflow run identifier is invalid.") from exc
    if normalized_run_id != run_id:
        raise ValueError("Workflow run identifier must be a canonical UUID.")
    workflow = raw_payload.get("workflow")
    if workflow not in {"content", "competitor", "outreach", "kpi"}:
        raise ValueError("Workflow name is invalid.")
    payload = raw_payload.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("Workflow input must be an object.")
    owner_id = raw_payload.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
        raise ValueError("Workflow owner scope is invalid.")
    encrypted_value = raw_payload.get("gateway_token_ciphertext")
    encrypted_token = None
    ttl_seconds = None
    if encrypted_value is not None:
        if not isinstance(encrypted_value, str) or len(encrypted_value) > 16_384:
            raise ValueError("Encrypted Gateway credential is invalid.")
        try:
            encrypted_token = base64.b64decode(
                encrypted_value.encode("ascii"), altchars=b"-_", validate=True
            )
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("Encrypted Gateway credential is invalid.") from exc
        ttl_seconds = raw_payload.get("gateway_token_ttl_seconds")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 60 <= ttl_seconds <= 1800:
            raise ValueError("Gateway credential lifetime is invalid.")
    return {
        "run_id": normalized_run_id,
        "workflow": workflow,
        "payload": payload,
        "owner_id": owner_id,
        "encrypted_gateway_token": encrypted_token,
        "gateway_token_ttl_seconds": ttl_seconds,
    }


async def _trigger_upstash_workflow(envelope: dict, run_id: str) -> str:
    """Trigger the signed Workflow route through the documented QStash REST API."""
    workflow_url = settings.upstash_workflow_url.rstrip("/") + "/internal/qstash/workflow"
    # QStash expects the destination URL directly after /v2/trigger/, not percent-encoded.
    trigger_url = settings.qstash_url.rstrip("/") + "/v2/trigger/" + workflow_url
    headers = {
        "Authorization": f"Bearer {settings.qstash_token}",
        "Content-Type": "application/json",
        "Upstash-Workflow-RunId": f"flowpilot-{run_id}",
        # Explicitly identify the v1 Workflow protocol used by workflow-py 2.x.
        "Upstash-Workflow-Sdk-Version": "1",
        "Upstash-Retries": "0",
        "Upstash-Flow-Control-Key": "flowpilot-global",
        # QStash requires the full flow-control value tuple, not parallelism alone.
        "Upstash-Flow-Control-Value": (
            f"parallelism={settings.qstash_workflow_parallelism}, rate=2, period=1s"
        ),
        "Upstash-Label": "flowpilot",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = await client.post(trigger_url, headers=headers, json=envelope)
    except httpx.HTTPError as exc:
        raise RuntimeError("QStash could not accept the workflow request.") from exc
    if not 200 <= response.status_code < 300:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {}
        error_detail = (
            error_payload.get("error")
            if isinstance(error_payload, dict)
            else None
        )
        if isinstance(error_detail, str) and error_detail.strip():
            error_detail = " ".join(error_detail.split())
            error_detail = re.sub(
                r"(?i)(authorization\s*[:=]\s*)bearer\s+[^\s,;]+",
                r"\1Bearer [REDACTED]",
                error_detail,
            )
            error_detail = re.sub(
                r"(?i)\bbearer\s+[^\s,;]+",
                "Bearer [REDACTED]",
                error_detail,
            )
            error_detail = re.sub(
                r"(?i)\b((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;]+",
                r"\1[REDACTED]",
                error_detail,
            )
            error_detail = error_detail[:240]
            raise RuntimeError(
                f"QStash rejected the workflow request (HTTP {response.status_code}): {error_detail}"
            )
        raise RuntimeError(
            f"QStash rejected the workflow request (HTTP {response.status_code}; no error detail)."
        )
    try:
        workflow_run_id = response.json().get("workflowRunId")
    except (ValueError, AttributeError):
        workflow_run_id = None
    if not isinstance(workflow_run_id, str) or not workflow_run_id:
        raise RuntimeError("QStash returned an invalid workflow acknowledgement.")
    return workflow_run_id


async def _cancel_upstash_workflow(run_id: str) -> None:
    """Cancel one QStash Workflow run without pausing the shared queue."""
    workflow_run_id = f"wfr_flowpilot-{run_id}"
    cancel_url = (
        settings.qstash_url.rstrip("/")
        + "/v2/workflows/runs/"
        + workflow_run_id
    )
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.delete(
                cancel_url,
                headers={"Authorization": f"Bearer {settings.qstash_token}"},
                params={"cancel": "true"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError("QStash could not confirm workflow cancellation.") from exc
    if not 200 <= response.status_code < 300:
        raise RuntimeError(
            f"QStash rejected workflow cancellation (HTTP {response.status_code})."
        )


if settings.qstash_workflow_enabled:
    from upstash_workflow import AsyncWorkflowContext
    from upstash_workflow.fastapi import Serve

    async def _wait_for_run_control(
        context: AsyncWorkflowContext[dict],
        run_id: str,
        owner_id: str,
        checkpoint: str,
    ) -> bool:
        """Wait durably while paused; return false if the run was stopped."""
        sleep_index = 0
        while True:
            run = await asyncio.to_thread(
                repo.get_run,
                run_id,
                owner_id=owner_id,
                include_all=True,
            )
            if not run or run.get("status") == "cancelled":
                return False
            if run.get("status") != "paused":
                return True
            # QStash sleep suspends this execution without occupying a Render
            # worker. Each iteration gets a stable replay-safe step name.
            await context.sleep(f"flowpilot-{checkpoint}-pause-{sleep_index}", 15)
            sleep_index += 1


    _workflow_serve = Serve(app)
    _workflow_environment = {
        "QSTASH_TOKEN": settings.qstash_token,
        "QSTASH_CURRENT_SIGNING_KEY": settings.qstash_current_signing_key,
        "QSTASH_NEXT_SIGNING_KEY": settings.qstash_next_signing_key,
        "QSTASH_URL": settings.qstash_url,
    }

    async def _upstash_workflow_failure(
        context: AsyncWorkflowContext[dict],
        fail_status: int,
        fail_response: str,
        fail_headers: dict,
    ) -> None:
        # Do not log or persist upstream response bodies, which may expose provider details.
        try:
            data = _validate_upstash_workflow_payload(
                _parse_upstash_workflow_initial_payload(context.request_payload)
            )
            await asyncio.to_thread(repo.ensure_pending_run, data["run_id"], data["workflow"], data["owner_id"])
            await asyncio.to_thread(repo.save_result, {
                "run_id": data["run_id"],
                "workflow": data["workflow"],
                "status": "failed",
                "output": {
                    "error": "Workflow execution failed after automatic retries.",
                    "recovery": "Check the workflow input or provider session, then submit a new run.",
                },
                "evidence": [],
                "duration_ms": 0,
                "created_at": datetime.now(UTC),
                "usage": None,
                "warnings": ["Upstash Workflow exhausted its configured execution retries."],
            })
            logger.warning("upstash_workflow_exhausted_retries run_id=%s status=%s", data["run_id"], int(fail_status))
        except Exception:
            logger.exception("upstash_workflow_failure_callback_failed")

    @_workflow_serve.post(
        "/internal/qstash/workflow",
        base_url=settings.upstash_workflow_url,
        retries=0,
        env=_workflow_environment,
        initial_payload_parser=_parse_upstash_workflow_initial_payload,
        failure_function=_upstash_workflow_failure,
    )
    async def _execute_upstash_workflow(context: AsyncWorkflowContext[dict]) -> None:
        data = _validate_upstash_workflow_payload(
            _parse_upstash_workflow_initial_payload(context.request_payload)
        )
        await asyncio.to_thread(repo.ensure_pending_run, data["run_id"], data["workflow"], data["owner_id"])

        if not await _wait_for_run_control(
            context, data["run_id"], data["owner_id"], "before-execute"
        ):
            return

        async def _execute_step() -> dict:
            result = await asyncio.to_thread(
                execute_run,
                data["run_id"],
                data["workflow"],
                data["payload"],
                encrypted_gateway_token=data["encrypted_gateway_token"],
                gateway_token_ttl_seconds=data["gateway_token_ttl_seconds"],
                persist_success=False,
                persist_failure=False,
            )
            return json.loads(_json_dumps(result))

        result = await context.run("execute-flowpilot-run", _execute_step)

        # A pause requested during a model call takes effect at this boundary.
        # The completed QStash step is replay-cached, so resume will not call
        # the provider a second time.
        if not await _wait_for_run_control(
            context, data["run_id"], data["owner_id"], "after-execute"
        ):
            return

        # Persist outside context.run so replays can restore local UI state.
        await asyncio.to_thread(repo.save_result, result)


@app.post(
    "/api/v1/runs",
    response_model=WorkflowResult,
    status_code=202,
    tags=["workflows"],
)
async def run_workflow(
    request: RunRequest,
    background_tasks: BackgroundTasks,
    llm_gateway_token: str = Header(
        default="",
        alias="X-LLM-Gateway-Token",
    ),
    principal: Principal = Depends(
        require_permission("runs:execute")
    ),
):
    gateway_token = (llm_gateway_token or "").strip()
    encrypted_gateway_token: bytes | None = None
    gateway_session_remaining: int | None = None

    if gateway_token:
        if len(gateway_token) > 8192 or any(char.isspace() for char in gateway_token):
            raise HTTPException(
                status_code=400,
                detail="The Gateway session token is invalid.",
            )
        gateway_session_remaining = verify_flowpilot_gateway_session(gateway_token)
        if settings.qstash_workflow_enabled:
            credential_ttl = min(settings.gateway_job_credential_ttl_seconds, int(gateway_session_remaining))
            try:
                encrypted_gateway_token = GatewayCredentialVault(
                    settings.gateway_job_credential_encryption_key, credential_ttl
                ).encrypt(gateway_token)
            except GatewayCredentialError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from None
        elif not settings.inline_execution:
            if not settings.database_url.strip():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Queued Gateway sessions require persistent PostgreSQL storage. "
                        "Use the service-level LLM key or configure PostgreSQL."
                    ),
                )
            try:
                vault = GatewayCredentialVault(
                    settings.gateway_job_credential_encryption_key,
                    settings.gateway_job_credential_ttl_seconds,
                )
                encrypted_gateway_token = vault.encrypt(gateway_token)
            except GatewayCredentialError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=str(exc),
                ) from None

    gateway_owner_scope = (
        getattr(gateway_session_remaining, "owner_scope", None)
        if gateway_token and gateway_session_remaining is not None
        else None
    )
    owner_id = gateway_owner_scope or principal.owner_id or hashlib.sha256(
        principal.subject.encode("utf-8")
    ).hexdigest()
    if settings.qstash_workflow_enabled:
        submitted_payload_size = len(
            json.dumps(request.payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )
        if submitted_payload_size > settings.qstash_workflow_max_payload_bytes - 20_000:
            raise HTTPException(status_code=413, detail="Workflow payload is too large for background delivery.")

    run_id = repo.create_pending_run(request.workflow.value, owner_id=owner_id)
    payload = dict(request.payload)
    credential_id: str | None = None

    if settings.qstash_workflow_enabled:
        credential_ttl = (
            min(settings.gateway_job_credential_ttl_seconds, int(gateway_session_remaining))
            if gateway_session_remaining is not None else None
        )
        envelope = {
            "run_id": run_id,
            "workflow": request.workflow.value,
            "payload": payload,
            "owner_id": owner_id,
            "gateway_token_ciphertext": (
                base64.urlsafe_b64encode(encrypted_gateway_token).decode("ascii")
                if encrypted_gateway_token is not None else None
            ),
            "gateway_token_ttl_seconds": credential_ttl,
        }
        try:
            await _trigger_upstash_workflow(envelope, run_id)
        except Exception:
            logger.exception("upstash_workflow_dispatch_failed run_id=%s", run_id)
            try:
                repo.save_result({
                    "run_id": run_id,
                    "workflow": request.workflow.value,
                    "status": "failed",
                    "output": {
                        "error": "Workflow could not be queued.",
                        "recovery": "QStash did not accept the request. Retry after checking the workflow connection.",
                    },
                    "evidence": [],
                    "duration_ms": 0,
                    "created_at": datetime.now(UTC),
                    "usage": None,
                    "warnings": ["Background dispatch failed before execution."],
                })
            except Exception:
                logger.exception("upstash_workflow_dispatch_failure_persist_failed run_id=%s", run_id)
            raise HTTPException(
                status_code=503,
                detail="Workflow could not be queued. Retry after checking the background execution connection.",
            ) from None
    elif settings.inline_execution:
        # Do not hold the HTTP request open for a potentially slow model call.
        # This in-process task is appropriate for the Free demo topology, which
        # already uses ephemeral in-memory run storage and does not provision RQ.
        background_tasks.add_task(
            _execute_inline_background,
            run_id,
            request.workflow.value,
            payload,
            gateway_token,
        )
    elif not settings.qstash_workflow_enabled:
        try:
            if encrypted_gateway_token is not None:
                credential_ttl = min(
                    settings.gateway_job_credential_ttl_seconds,
                    gateway_session_remaining or settings.gateway_job_credential_ttl_seconds,
                )
                credential_id = repo.store_gateway_job_credential(
                    run_id,
                    encrypted_gateway_token,
                    credential_ttl,
                )

            enqueue_kwargs = {
                "job_id": run_id,
                "job_timeout": settings.workflow_job_timeout_seconds,
            }
            if credential_id is not None:
                # Redis receives only this opaque reference, never the bearer.
                enqueue_kwargs["gateway_credential_id"] = credential_id

            queue().enqueue(
                execute_run,
                run_id,
                request.workflow.value,
                payload,
                **enqueue_kwargs,
            )
        except Exception:
            if credential_id is not None:
                try:
                    repo.delete_gateway_job_credential(run_id, credential_id)
                except Exception:
                    logger.exception(
                        "gateway_credential_cleanup_failed run_id=%s",
                        run_id,
                    )
            logger.exception("workflow_enqueue_failed run_id=%s", run_id)
            try:
                repo.save_result({
                    "run_id": run_id,
                    "workflow": request.workflow.value,
                    "status": "failed",
                    "output": {
                        "error": "Workflow could not be queued.",
                        "recovery": "Check Redis and worker availability, then retry.",
                    },
                    "evidence": [],
                    "duration_ms": 0,
                    "created_at": datetime.now(UTC),
                    "usage": None,
                    "warnings": ["Queue dispatch failed before worker execution."],
                })
            except Exception:
                logger.exception("workflow_dispatch_failure_persist_failed run_id=%s", run_id)
            raise HTTPException(
                status_code=503,
                detail="Workflow could not be queued. Check queue availability and retry.",
            ) from None

    result = repo.get_run(run_id)
    if not result:
        raise HTTPException(
            status_code=500,
            detail="Failed to create workflow run",
        )
    return result


@app.get(
    "/api/v1/approvals",
    tags=["approvals"],
)
def list_pending_approvals(
    limit: int = 50,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("approvals:decide")),
):
    """List pending review requests inside the verified owner's scope."""
    gateway_token = (llm_gateway_token or "").strip()
    gateway_scope = (
        verify_flowpilot_gateway_session(gateway_token).owner_scope
        if gateway_token
        else None
    )
    owner_id = gateway_scope or principal.owner_id or hashlib.sha256(
        principal.subject.encode("utf-8")
    ).hexdigest()
    try:
        safe_limit = min(max(limit, 1), 100)
        approvals = repo.list_pending_approvals(
            safe_limit,
            owner_id=owner_id,
            include_all=principal.role == "admin",
        )
        return {"approvals": approvals, "total": len(approvals)}
    except Exception:
        logger.exception("pending_approvals_lookup_failed")
        raise HTTPException(
            status_code=503,
            detail="Pending approvals are temporarily unavailable",
        ) from None


@app.get(
    "/api/v1/runs",
    response_model=RunListResponse,
    tags=["workflows"],
)
def list_runs(
    limit: int = 20,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:read")),
):
    safe_limit = min(max(limit, 1), 100)
    gateway_token = (llm_gateway_token or "").strip()
    gateway_scope = (
        verify_flowpilot_gateway_session(gateway_token).owner_scope
        if gateway_token
        else None
    )
    owner_id = gateway_scope or principal.owner_id or hashlib.sha256(
        principal.subject.encode("utf-8")
    ).hexdigest()

    try:
        runs = repo.list_runs(
            safe_limit,
            owner_id=owner_id,
            include_all=principal.role == "admin",
        )

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


def _owned_run_for_control(
    run_id: str,
    llm_gateway_token: str,
    principal: Principal,
) -> dict:
    gateway_token = (llm_gateway_token or "").strip()
    gateway_scope = (
        verify_flowpilot_gateway_session(gateway_token).owner_scope
        if gateway_token
        else None
    )
    owner_id = gateway_scope or principal.owner_id or hashlib.sha256(
        principal.subject.encode("utf-8")
    ).hexdigest()
    result = repo.get_run(
        run_id,
        owner_id=owner_id,
        include_all=principal.role == "admin",
    )
    if not result:
        raise HTTPException(status_code=404, detail="Run not found")
    return result


@app.post("/api/v1/runs/{run_id}/pause", tags=["workflows"])
def pause_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:execute")),
):
    if not settings.qstash_workflow_enabled:
        raise HTTPException(status_code=409, detail="Per-run controls require QStash Workflow execution.")
    current = _owned_run_for_control(run_id, llm_gateway_token, principal)
    if not repo.pause_run(run_id):
        raise HTTPException(status_code=409, detail=f"Run cannot be paused from status '{current['status']}'.")
    logger.info("workflow_paused run_id=%s", run_id)
    return {"run_id": run_id, "status": "paused"}


@app.post("/api/v1/runs/{run_id}/resume", tags=["workflows"])
def resume_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:execute")),
):
    if not settings.qstash_workflow_enabled:
        raise HTTPException(status_code=409, detail="Per-run controls require QStash Workflow execution.")
    current = _owned_run_for_control(run_id, llm_gateway_token, principal)
    if not repo.resume_run(run_id):
        raise HTTPException(status_code=409, detail=f"Run cannot be resumed from status '{current['status']}'.")
    resumed = repo.get_run(
        run_id,
        owner_id=current.get("owner_id") or principal.owner_id,
        include_all=principal.role == "admin",
    )
    logger.info("workflow_resumed run_id=%s", run_id)
    return {"run_id": run_id, "status": (resumed or {}).get("status", "queued")}


@app.post("/api/v1/runs/{run_id}/stop", tags=["workflows"])
async def stop_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:execute")),
):
    if not settings.qstash_workflow_enabled:
        raise HTTPException(status_code=409, detail="Per-run controls require QStash Workflow execution.")
    current = _owned_run_for_control(run_id, llm_gateway_token, principal)
    if not repo.stop_run(run_id):
        raise HTTPException(status_code=409, detail=f"Run cannot be stopped from status '{current['status']}'.")
    try:
        await _cancel_upstash_workflow(run_id)
    except RuntimeError:
        # The local cancellation is already authoritative and protects against
        # late results even when QStash's remote cancellation races or fails.
        logger.warning("workflow_remote_cancel_unconfirmed run_id=%s", run_id)
        raise HTTPException(
            status_code=502,
            detail="The run was marked canceled, but QStash could not confirm remote cancellation.",
        ) from None
    logger.info("workflow_stopped run_id=%s", run_id)
    return {"run_id": run_id, "status": "cancelled"}


@app.get(
    "/api/v1/runs/{run_id}",
    response_model=WorkflowResult,
    tags=["workflows"],
)
def get_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:read")),
):
    gateway_token = (llm_gateway_token or "").strip()
    gateway_scope = (
        verify_flowpilot_gateway_session(gateway_token).owner_scope
        if gateway_token
        else None
    )
    owner_id = gateway_scope or principal.owner_id or hashlib.sha256(
        principal.subject.encode("utf-8")
    ).hexdigest()
    try:
        result = repo.get_run(
            run_id,
            owner_id=owner_id,
            include_all=principal.role == "admin",
        )

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
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("approvals:decide")),
):
    gateway_token = (llm_gateway_token or "").strip()
    gateway_scope = (
        verify_flowpilot_gateway_session(gateway_token).owner_scope
        if gateway_token
        else None
    )
    owner_id = gateway_scope or principal.owner_id or hashlib.sha256(
        principal.subject.encode("utf-8")
    ).hexdigest()
    result = repo.get_run(
        run_id,
        owner_id=owner_id,
        include_all=principal.role == "admin",
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Run not found",
        )

    if result["workflow"] not in {"content", "outreach"}:
        raise HTTPException(
            status_code=400,
            detail="This workflow has no external-impact action requiring approval",
        )

    # The authenticated principal determines reviewer identity.
    # The legacy request.reviewer field is deliberately ignored.
    try:
        return repo.create_approval(
            run_id,
            request.decision,
            principal.subject,
            request.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
