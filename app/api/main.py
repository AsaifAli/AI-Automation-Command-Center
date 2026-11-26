
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
from qstash import Receiver
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
            "api -> QStash HTTP message -> signed execution endpoint + native failure callback -> "
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
            "qstash_http_queue"
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
            "qstash_http_queue"
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



def _validate_qstash_payload(raw_payload: object) -> dict:
    """Validate a signed QStash message envelope before executing a run."""
    if not isinstance(raw_payload, dict):
        raise TypeError("QStash message body must be a JSON object.")
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
    generation = raw_payload.get("dispatch_generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError("QStash dispatch generation is invalid.")

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
        "dispatch_generation": generation,
        "encrypted_gateway_token": encrypted_token,
        "gateway_token_ttl_seconds": ttl_seconds,
    }


def _verify_qstash_signature(
    body: bytes,
    signature: str | None,
    expected_url: str,
) -> None:
    """Verify body and exact destination URL against either active QStash key."""
    if not signature:
        raise HTTPException(status_code=401, detail="Missing QStash signature.")
    try:
        raw_text = body.decode("utf-8")
        Receiver(
            current_signing_key=settings.qstash_current_signing_key,
            next_signing_key=settings.qstash_next_signing_key,
        ).verify(
            signature=signature,
            body=raw_text,
            url=expected_url,
            clock_tolerance=5,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid QStash signature.") from None


async def _publish_qstash_message(
    envelope: dict,
    run_id: str,
    generation: int,
) -> str:
    """Publish one signed HTTP job; no Workflow SDK trigger or replay callback is involved."""
    destination_url = (
        settings.upstash_workflow_url.rstrip("/") + "/internal/qstash/execute"
    )
    failure_url = (
        settings.upstash_workflow_url.rstrip("/") + "/internal/qstash/failure"
    )
    publish_url = settings.qstash_url.rstrip("/") + "/v2/publish/" + destination_url
    headers = {
        "Authorization": f"Bearer {settings.qstash_token}",
        "Content-Type": "application/json",
        "Upstash-Retries": "0",
        # The free QStash plan permits up to 15 minutes per HTTP response.
        # Leave a margin so QStash can invoke the failure callback on timeout.
        "Upstash-Timeout": "14m",
        "Upstash-Failure-Callback": failure_url,
        "Upstash-Failure-Callback-Retries": "2",
        "Upstash-Failure-Callback-Timeout": "10s",
        "Upstash-Flow-Control-Key": "flowpilot-global",
        "Upstash-Flow-Control-Value": (
            f"parallelism={settings.qstash_workflow_parallelism}, rate=2, period=1s"
        ),
        "Upstash-Deduplication-Id": f"flowpilot-{run_id}-{generation}",
        "Upstash-Label": "flowpilot",
    }
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(publish_url, headers=headers, json=envelope)
    except httpx.HTTPError as exc:
        raise RuntimeError("QStash could not accept the workflow message.") from exc

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
            raise RuntimeError(
                f"QStash rejected the workflow message (HTTP {response.status_code}): {error_detail[:240]}"
            )
        raise RuntimeError(
            f"QStash rejected the workflow message (HTTP {response.status_code}; no error detail)."
        )

    try:
        message_id = response.json().get("messageId")
    except (ValueError, AttributeError):
        message_id = None
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("QStash returned an invalid message acknowledgement.")
    return message_id


async def _cancel_qstash_message(message_id: str) -> bool:
    """Cancel a pending QStash message. False means it was already delivered or gone."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", message_id or ""):
        raise RuntimeError("QStash message identifier is invalid.")
    cancel_url = settings.qstash_url.rstrip("/") + "/v2/messages/" + message_id
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.delete(
                cancel_url,
                headers={"Authorization": f"Bearer {settings.qstash_token}"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError("QStash could not confirm message cancellation.") from exc
    if response.status_code == 404:
        return False
    if response.status_code not in {200, 202}:
        raise RuntimeError(
            f"QStash rejected message cancellation (HTTP {response.status_code})."
        )
    try:
        return int(response.json().get("cancelled", 0)) > 0
    except (ValueError, TypeError, AttributeError):
        return True


@app.post("/internal/qstash/execute", include_in_schema=False)
async def execute_qstash_message(request: Request):
    """Execute one QStash message and acknowledge its delivery exactly once."""
    body = await request.body()
    _verify_qstash_signature(
        body,
        request.headers.get("Upstash-Signature"),
        settings.upstash_workflow_url.rstrip("/") + request.url.path,
    )
    try:
        envelope = json.loads(body.decode("utf-8"))
        data = _validate_qstash_payload(envelope)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid QStash workflow message.") from None

    await asyncio.to_thread(
        repo.ensure_pending_run,
        data["run_id"],
        data["workflow"],
        data["owner_id"],
    )
    await asyncio.to_thread(repo.restore_qstash_dispatch, data["run_id"], envelope)
    claim = await asyncio.to_thread(
        repo.claim_qstash_run,
        data["run_id"],
        data["dispatch_generation"],
    )
    if claim != "claimed":
        # Duplicate, superseded, paused, and canceled deliveries are acknowledged
        # without executing the model a second time.
        return {"run_id": data["run_id"], "status": claim}

    try:
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
    except Exception:
        # QStash's native failure callback records a terminal state after this
        # delivery fails. Never log the incoming payload or encrypted credential.
        logger.exception("qstash_message_execution_failed run_id=%s", data["run_id"])
        raise HTTPException(
            status_code=500,
            detail="Workflow execution failed before a result could be persisted.",
        ) from None

    latest = await asyncio.to_thread(
        repo.get_run,
        data["run_id"],
        owner_id=data["owner_id"],
        include_all=True,
    )
    latest_status = (latest or {}).get("status")
    if latest_status == "paused":
        await asyncio.to_thread(repo.save_paused_result, data["run_id"], result)
        return {"run_id": data["run_id"], "status": "paused"}
    if latest_status == "cancelled":
        return {"run_id": data["run_id"], "status": "cancelled"}

    await asyncio.to_thread(repo.save_result, result)
    return {"run_id": data["run_id"], "status": result["status"]}


@app.post("/internal/qstash/failure", include_in_schema=False)
async def record_qstash_failure(request: Request):
    """Record QStash delivery timeout/failure without the Workflow SDK callback contract."""
    body = await request.body()
    _verify_qstash_signature(body, request.headers.get("Upstash-Signature"))
    try:
        callback = json.loads(body.decode("utf-8"))
        source_body = callback.get("sourceBody") if isinstance(callback, dict) else None
        if not isinstance(source_body, str) or len(source_body) > 1_400_000:
            raise ValueError("QStash failure callback has no valid source body.")
        decoded = base64.b64decode(source_body, validate=True)
        envelope = json.loads(decoded.decode("utf-8"))
        data = _validate_qstash_payload(envelope)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid QStash failure callback.") from None

    await asyncio.to_thread(
        repo.ensure_pending_run,
        data["run_id"],
        data["workflow"],
        data["owner_id"],
    )
    await asyncio.to_thread(repo.restore_qstash_dispatch, data["run_id"], envelope)
    failure = {
        "run_id": data["run_id"],
        "workflow": data["workflow"],
        "status": "failed",
        "output": {
            "error": "QStash could not complete delivery to the FlowPilot API.",
            "failure_code": "qstash_delivery_failed",
            "recovery": "Check the API service and model provider, then submit a new run.",
        },
        "evidence": [],
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": ["QStash exhausted the configured HTTP delivery attempt."],
    }
    recorded = await asyncio.to_thread(repo.fail_run_if_active, failure)
    logger.warning(
        "qstash_delivery_failure_recorded run_id=%s status=%s recorded=%s",
        data["run_id"],
        int(callback.get("status", 0)),
        recorded,
    )
    return {"run_id": data["run_id"], "status": "failure_recorded" if recorded else "unchanged"}


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
            generation = repo.prepare_qstash_dispatch(run_id, envelope)
            message_id = await _publish_qstash_message(envelope, run_id, generation)
            repo.set_qstash_message_id(run_id, generation, message_id)
        except Exception:
            logger.exception("qstash_message_dispatch_failed run_id=%s", run_id)
            repo.save_result({
                "run_id": run_id,
                "workflow": request.workflow.value,
                "status": "failed",
                "output": {
                    "error": "Workflow could not be queued.",
                    "recovery": "QStash did not accept the message. Retry after checking the queue connection.",
                },
                "evidence": [],
                "duration_ms": 0,
                "created_at": datetime.now(UTC),
                "usage": None,
                "warnings": ["Message dispatch failed before execution."],
            })
            raise HTTPException(
                status_code=503,
                detail="Workflow could not be queued. Check the QStash connection and retry.",
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
async def pause_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:execute")),
):
    if not settings.qstash_workflow_enabled:
        raise HTTPException(status_code=409, detail="Per-run controls require the QStash message queue.")
    current = _owned_run_for_control(run_id, llm_gateway_token, principal)
    dispatch = repo.get_qstash_dispatch(run_id) or {}
    if not repo.pause_run(run_id):
        raise HTTPException(status_code=409, detail=f"Run cannot be paused from status '{current['status']}'.")
    cancelled = False
    if current["status"] == "queued" and dispatch.get("message_id"):
        try:
            cancelled = await _cancel_qstash_message(dispatch["message_id"])
        except RuntimeError:
            logger.warning("qstash_pause_cancel_unconfirmed run_id=%s", run_id)
    logger.info("workflow_paused run_id=%s queued_message_cancelled=%s", run_id, cancelled)
    return {
        "run_id": run_id,
        "status": "paused",
        "queued_message_cancelled": cancelled,
        "note": "An in-flight model request may finish; its result is held until Resume.",
    }


@app.post("/api/v1/runs/{run_id}/resume", tags=["workflows"])
async def resume_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:execute")),
):
    if not settings.qstash_workflow_enabled:
        raise HTTPException(status_code=409, detail="Per-run controls require the QStash message queue.")
    current = _owned_run_for_control(run_id, llm_gateway_token, principal)
    dispatch = repo.get_qstash_dispatch(run_id) or {}
    resume_status = current.get("resume_status") or "queued"
    if not repo.resume_run(run_id):
        raise HTTPException(status_code=409, detail=f"Run cannot be resumed from status '{current['status']}'.")

    pending_result = repo.take_pending_result(run_id)
    if pending_result is not None:
        repo.save_result(pending_result)
        resumed = repo.get_run(
            run_id,
            owner_id=current.get("owner_id") or principal.owner_id,
            include_all=principal.role == "admin",
        )
        logger.info("workflow_resumed_from_checkpoint run_id=%s", run_id)
        return {"run_id": run_id, "status": (resumed or {}).get("status", "completed"), "requeued": False}

    if resume_status == "queued":
        envelope = dispatch.get("payload")
        if not isinstance(envelope, dict):
            raise HTTPException(
                status_code=409,
                detail="The queued message payload is unavailable; submit a new workflow run.",
            )
        try:
            generation = repo.prepare_qstash_dispatch(run_id, envelope)
            message_id = await _publish_qstash_message(envelope, run_id, generation)
            repo.set_qstash_message_id(run_id, generation, message_id)
        except Exception:
            logger.exception("qstash_resume_dispatch_failed run_id=%s", run_id)
            failure = {
                "run_id": run_id,
                "workflow": current["workflow"],
                "status": "failed",
                "output": {"error": "Workflow could not be resumed.", "failure_code": "resume_dispatch_failed"},
                "evidence": [],
                "duration_ms": 0,
                "created_at": datetime.now(UTC),
                "usage": None,
                "warnings": ["Resume could not enqueue a new QStash message."],
            }
            repo.fail_run_if_active(failure)
            raise HTTPException(status_code=503, detail="Workflow could not be resumed through QStash.") from None

    resumed = repo.get_run(
        run_id,
        owner_id=current.get("owner_id") or principal.owner_id,
        include_all=principal.role == "admin",
    )
    logger.info("workflow_resumed run_id=%s resume_status=%s", run_id, resume_status)
    return {"run_id": run_id, "status": (resumed or {}).get("status", "queued"), "requeued": resume_status == "queued"}


@app.post("/api/v1/runs/{run_id}/stop", tags=["workflows"])
async def stop_run(
    run_id: str,
    llm_gateway_token: str = Header(default="", alias="X-LLM-Gateway-Token"),
    principal: Principal = Depends(require_permission("runs:execute")),
):
    if not settings.qstash_workflow_enabled:
        raise HTTPException(status_code=409, detail="Per-run controls require the QStash message queue.")
    current = _owned_run_for_control(run_id, llm_gateway_token, principal)
    dispatch = repo.get_qstash_dispatch(run_id) or {}
    if not repo.stop_run(run_id):
        raise HTTPException(status_code=409, detail=f"Run cannot be stopped from status '{current['status']}'.")
    cancellation_confirmed = False
    if dispatch.get("message_id"):
        try:
            cancellation_confirmed = await _cancel_qstash_message(dispatch["message_id"])
        except RuntimeError:
            logger.warning("qstash_stop_cancel_unconfirmed run_id=%s", run_id)
    logger.info("workflow_stopped run_id=%s cancellation_confirmed=%s", run_id, cancellation_confirmed)
    return {
        "run_id": run_id,
        "status": "cancelled",
        "remote_cancellation_confirmed": cancellation_confirmed,
    }


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
