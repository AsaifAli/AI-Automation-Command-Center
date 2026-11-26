import copy
import json
import logging
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import psycopg
from psycopg.rows import dict_row

from app.core.config import Settings
from app.storage.redis_state import RedisStateStore

logger = logging.getLogger(__name__)


def _json_default(value: Any):
    """Serialize values that can legitimately appear in workflow payloads/results."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _normalize_usage(usage: Any) -> dict[str, Any] | None:
    """Return a schema-compatible usage object for new and legacy run records.

    Older portfolio releases persisted usage without provider/model metadata.
    Normalize at the repository boundary so API responses remain backward-compatible.
    """
    if not usage:
        return None
    normalized = dict(usage)
    normalized.setdefault("provider", "unknown")
    normalized.setdefault("model", None)
    normalized.setdefault("prompt_tokens", 0)
    normalized.setdefault("completion_tokens", 0)
    normalized.setdefault(
        "total_tokens",
        int(normalized.get("prompt_tokens", 0)) + int(normalized.get("completion_tokens", 0)),
    )
    normalized.setdefault("estimated_cost_usd", 0.0)
    normalized.setdefault("cost_estimate_available", True)
    return normalized


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'legacy',
    workflow VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    resume_status VARCHAR(50),
    dispatch_payload_json JSONB,
    qstash_message_id TEXT,
    rq_job_id TEXT,
    dispatch_generation INTEGER NOT NULL DEFAULT 0,
    pending_result_json JSONB,
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    usage_json JSONB,
    warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT
);
-- Lightweight in-place migration for databases created by earlier portfolio releases.
-- This keeps `docker compose up` upgrade-safe without requiring users to delete data.
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS resume_status VARCHAR(50);
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS dispatch_payload_json JSONB;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS qstash_message_id TEXT;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS rq_job_id TEXT;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS dispatch_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS pending_result_json JSONB;
-- Before owner scopes existed, all authorized API roles shared one private control plane.
-- Backfill those pre-migration rows to that same owner so existing history remains visible.
UPDATE workflow_runs
SET owner_id = 'flowpilot-control-plane'
WHERE owner_id = 'legacy';
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS output_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS duration_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS usage_json JSONB;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS error TEXT;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_created_at ON workflow_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_owner_created_at ON workflow_runs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    decision VARCHAR(20) NOT NULL,
    reviewer VARCHAR(100) NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    decided_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    event VARCHAR(100) NOT NULL,
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS detail_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS gateway_job_credentials (
    credential_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    encrypted_token BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gateway_job_credentials_expires_at
    ON gateway_job_credentials(expires_at);
-- These tables are backend-owned workflow state, not a browser Data API.
-- The application connects using the database owner role; browser roles receive no access.
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE gateway_job_credentials ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE workflow_runs, approvals, audit_events, gateway_job_credentials FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON TABLE workflow_runs, approvals, audit_events, gateway_job_credentials FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON TABLE workflow_runs, approvals, audit_events, gateway_job_credentials FROM authenticated';
    END IF;
END
$$;
"""


class Repository:
    """Workflow state repository with optional PostgreSQL or ephemeral memory."""

    _memory_runs: ClassVar[dict[str, dict[str, Any]]] = {}
    _memory_approvals: ClassVar[dict[str, dict[str, Any]]] = {}
    _memory_lock: ClassVar[Any] = threading.RLock()

    def __init__(self, settings: Settings):
        self.settings = settings
        self._redis_state = (
            RedisStateStore(settings)
            if getattr(settings, "redis_state_enabled", False) and not settings.database_url.strip()
            else None
        )
        self._use_memory = not bool(settings.database_url.strip()) and self._redis_state is None

    def _connect(self):
        if self._use_memory or self._redis_state is not None:
            raise RuntimeError("This repository backend does not use a PostgreSQL connection")
        return psycopg.connect(self.settings.database_url, row_factory=dict_row)

    def ping(self) -> bool:
        """Check the configured state backend without scanning workflow records."""
        if self._redis_state is not None:
            return self._redis_state.ping()
        if self._use_memory:
            return True
        with self._connect() as conn:
            conn.execute("SELECT 1")
        return True

    def init_db(self) -> None:
        if self._redis_state is not None:
            self._redis_state.init_db()
            return
        if self._use_memory:
            return
        with self._connect() as conn:
            conn.execute(SCHEMA)
            conn.execute(
                "DELETE FROM gateway_job_credentials WHERE expires_at <= %s",
                (datetime.now(UTC),),
            )

    def create_pending_run(self, workflow: str, owner_id: str = "legacy") -> str:
        if self._redis_state is not None:
            return self._redis_state.create_pending_run(workflow, owner_id=owner_id)
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = {
            "run_id": run_id,
            "owner_id": owner_id,
            "workflow": workflow,
            "status": "queued",
            "resume_status": None,
            "dispatch_payload": None,
            "qstash_message_id": None,
            "rq_job_id": None,
            "dispatch_generation": 0,
            "pending_result": None,
            "output": {},
            "evidence": [],
            "duration_ms": 0,
            "created_at": now,
            "usage": None,
            "warnings": [],
        }
        if self._use_memory:
            with self._memory_lock:
                self._memory_runs[run_id] = record
            return run_id
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workflow_runs(run_id,owner_id,workflow,status,created_at) VALUES (%s,%s,%s,%s,%s)",
                (run_id, owner_id, workflow, "queued", now),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "queued", json.dumps({"workflow": workflow}), now),
            )
        return run_id


    def ensure_pending_run(
        self,
        run_id: str,
        workflow: str,
        owner_id: str,
    ) -> None:
        """Idempotently restore a queued run for a verified Workflow callback."""
        if self._redis_state is not None:
            return self._redis_state.ensure_pending_run(run_id, workflow, owner_id)
        try:
            normalized_run_id = str(uuid.UUID(str(run_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Workflow run identifier is invalid.") from exc
        if normalized_run_id != str(run_id):
            raise ValueError("Workflow run identifier must be a canonical UUID.")
        if not isinstance(workflow, str) or not workflow.strip():
            raise ValueError("Workflow name is required.")
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("Workflow owner scope is invalid.")
        now = datetime.now(UTC)
        if self._use_memory:
            with self._memory_lock:
                existing = self._memory_runs.get(normalized_run_id)
                if existing:
                    if existing.get("workflow") != workflow or existing.get("owner_id") != owner_id:
                        raise ValueError("Workflow run scope does not match the existing run.")
                    return
                self._memory_runs[normalized_run_id] = {
                    "run_id": normalized_run_id,
                    "owner_id": owner_id,
                    "workflow": workflow,
                    "status": "queued",
                    "resume_status": None,
                    "dispatch_payload": None,
                    "qstash_message_id": None,
                    "rq_job_id": None,
                    "dispatch_generation": 0,
                    "pending_result": None,
                    "output": {},
                    "evidence": [],
                    "duration_ms": 0,
                    "created_at": now,
                    "usage": None,
                    "warnings": [],
                }
            return
        with self._connect() as conn:
            inserted = conn.execute(
                """INSERT INTO workflow_runs(run_id,owner_id,workflow,status,created_at)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (run_id) DO NOTHING
                   RETURNING run_id""",
                (normalized_run_id, owner_id, workflow, "queued", now),
            ).fetchone()
            existing = conn.execute(
                "SELECT workflow, owner_id FROM workflow_runs WHERE run_id=%s",
                (normalized_run_id,),
            ).fetchone()
            if not existing:
                raise RuntimeError("Workflow run could not be restored.")
            if existing["workflow"] != workflow or existing["owner_id"] != owner_id:
                raise ValueError("Workflow run scope does not match the existing run.")
            if inserted:
                conn.execute(
                    """INSERT INTO audit_events(run_id,event,detail_json,created_at)
                       VALUES (%s,%s,%s,%s)""",
                    (normalized_run_id, "queued", json.dumps({"workflow": workflow}), now),
                )

    def prepare_qstash_dispatch(self, run_id: str, payload: dict[str, Any]) -> int:
        """Prepare a new generation of a plain QStash message for this run."""
        if self._redis_state is not None:
            return self._redis_state.prepare_qstash_dispatch(run_id, payload)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or run.get("status") != "queued":
                    raise ValueError("Only queued runs can be dispatched.")
                generation = int(run.get("dispatch_generation", 0)) + 1
                payload["dispatch_generation"] = generation
                run["dispatch_generation"] = generation
                run["dispatch_payload"] = copy.deepcopy(payload)
                run["qstash_message_id"] = None
                return generation

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, dispatch_generation FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row or row["status"] != "queued":
                raise ValueError("Only queued runs can be dispatched.")
            generation = int(row["dispatch_generation"] or 0) + 1
            payload["dispatch_generation"] = generation
            conn.execute(
                """UPDATE workflow_runs
                   SET dispatch_payload_json=%s, dispatch_generation=%s, qstash_message_id=NULL
                   WHERE run_id=%s""",
                (_json_dumps(payload), generation, run_id),
            )
        return generation

    def prepare_rq_dispatch(self, run_id: str, payload: dict[str, Any]) -> int:
        """Persist the complete RQ task envelope before making it visible to Redis.

        Reusing the dispatch payload column lets a paused queued run be recreated
        after the original RQ job has left its queue registry or expired.
        """
        if self._redis_state is not None:
            return self._redis_state.prepare_rq_dispatch(run_id, payload)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or run.get("status") != "queued":
                    raise ValueError("Only queued runs can be dispatched.")
                generation = int(run.get("dispatch_generation", 0)) + 1
                payload["dispatch_generation"] = generation
                run["dispatch_generation"] = generation
                run["dispatch_payload"] = copy.deepcopy(payload)
                run["rq_job_id"] = None
                return generation

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, dispatch_generation FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row or row["status"] != "queued":
                raise ValueError("Only queued runs can be dispatched.")
            generation = int(row["dispatch_generation"] or 0) + 1
            payload["dispatch_generation"] = generation
            conn.execute(
                """UPDATE workflow_runs
                   SET dispatch_payload_json=%s, dispatch_generation=%s, rq_job_id=NULL
                   WHERE run_id=%s""",
                (_json_dumps(payload), generation, run_id),
            )
        return generation

    def set_rq_job_id(self, run_id: str, generation: int, job_id: str) -> bool:
        if self._redis_state is not None:
            return self._redis_state.set_rq_job_id(run_id, generation, job_id)
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 256:
            raise ValueError("RQ job identifier is invalid.")
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or int(run.get("dispatch_generation", 0)) != generation:
                    return False
                run["rq_job_id"] = job_id
                return True
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE workflow_runs SET rq_job_id=%s
                   WHERE run_id=%s AND dispatch_generation=%s RETURNING run_id""",
                (job_id, run_id, generation),
            ).fetchone()
        return bool(row)

    def claim_rq_run(self, run_id: str, generation: int, job_id: str) -> bool:
        """Claim the currently persisted RQ generation, allowing RQ's own safe replay."""
        if self._redis_state is not None:
            return self._redis_state.claim_rq_run(run_id, generation, job_id)
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            return False
        if not isinstance(job_id, str) or not job_id.strip():
            return False
        now = datetime.now(UTC)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return False
                if (
                    int(run.get("dispatch_generation", 0)) != generation
                    or run.get("rq_job_id") != job_id
                ):
                    return False
                status = run.get("status")
                if status == "queued":
                    run["status"] = "running"
                    return True
                # RQ can redeliver the same job after a worker process dies. It is
                # safe to replay this generation because RQ has recovered the job.
                return status == "running"

        with self._connect() as conn:
            row = conn.execute(
                """SELECT status FROM workflow_runs
                   WHERE run_id=%s AND dispatch_generation=%s AND rq_job_id=%s
                   FOR UPDATE""",
                (run_id, generation, job_id),
            ).fetchone()
            if not row:
                return False
            if row["status"] == "running":
                return True
            if row["status"] != "queued":
                return False
            changed = conn.execute(
                """UPDATE workflow_runs SET status='running'
                   WHERE run_id=%s AND dispatch_generation=%s AND rq_job_id=%s
                     AND status='queued' RETURNING run_id""",
                (run_id, generation, job_id),
            ).fetchone()
            if changed:
                conn.execute(
                    "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                    (run_id, "started", _json_dumps({"queue": "rq", "generation": generation}), now),
                )
        return bool(changed)

    def get_rq_dispatch(self, run_id: str) -> dict[str, Any] | None:
        if self._redis_state is not None:
            return self._redis_state.get_rq_dispatch(run_id)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return None
                return {
                    "payload": copy.deepcopy(run.get("dispatch_payload")),
                    "job_id": run.get("rq_job_id"),
                    "generation": int(run.get("dispatch_generation", 0)),
                    "pending_result": copy.deepcopy(run.get("pending_result")),
                    "status": run.get("status"),
                    "resume_status": run.get("resume_status"),
                }
        with self._connect() as conn:
            row = conn.execute(
                """SELECT dispatch_payload_json, rq_job_id, dispatch_generation,
                          pending_result_json, status, resume_status
                   FROM workflow_runs WHERE run_id=%s""",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "payload": row["dispatch_payload_json"],
            "job_id": row["rq_job_id"],
            "generation": int(row["dispatch_generation"] or 0),
            "pending_result": row["pending_result_json"],
            "status": row["status"],
            "resume_status": row["resume_status"],
        }

    def restore_qstash_dispatch(self, run_id: str, payload: dict[str, Any]) -> None:
        """Restore dispatch metadata from an authenticated QStash delivery after process restart."""
        if self._redis_state is not None:
            return self._redis_state.restore_qstash_dispatch(run_id, payload)
        generation = payload.get("dispatch_generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError("QStash dispatch generation is invalid.")
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    raise KeyError(f"Run not found: {run_id}")
                current = int(run.get("dispatch_generation", 0))
                if generation > current:
                    run["dispatch_generation"] = generation
                    run["dispatch_payload"] = copy.deepcopy(payload)
            return

        with self._connect() as conn:
            conn.execute(
                """UPDATE workflow_runs
                   SET dispatch_generation=%s,
                       dispatch_payload_json=COALESCE(dispatch_payload_json, %s)
                   WHERE run_id=%s AND dispatch_generation < %s""",
                (generation, _json_dumps(payload), run_id, generation),
            )

    def set_qstash_message_id(self, run_id: str, generation: int, message_id: str) -> bool:
        if self._redis_state is not None:
            return self._redis_state.set_qstash_message_id(run_id, generation, message_id)
        if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 256:
            raise ValueError("QStash message identifier is invalid.")
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or int(run.get("dispatch_generation", 0)) != generation:
                    return False
                run["qstash_message_id"] = message_id
                return True
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE workflow_runs SET qstash_message_id=%s
                   WHERE run_id=%s AND dispatch_generation=%s RETURNING run_id""",
                (message_id, run_id, generation),
            ).fetchone()
        return bool(row)

    def get_qstash_dispatch(self, run_id: str) -> dict[str, Any] | None:
        if self._redis_state is not None:
            return self._redis_state.get_qstash_dispatch(run_id)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return None
                return {
                    "payload": copy.deepcopy(run.get("dispatch_payload")),
                    "message_id": run.get("qstash_message_id"),
                    "generation": int(run.get("dispatch_generation", 0)),
                    "pending_result": copy.deepcopy(run.get("pending_result")),
                    "status": run.get("status"),
                    "resume_status": run.get("resume_status"),
                }
        with self._connect() as conn:
            row = conn.execute(
                """SELECT dispatch_payload_json, qstash_message_id, dispatch_generation,
                          pending_result_json, status, resume_status
                   FROM workflow_runs WHERE run_id=%s""",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "payload": row["dispatch_payload_json"],
            "message_id": row["qstash_message_id"],
            "generation": int(row["dispatch_generation"] or 0),
            "pending_result": row["pending_result_json"],
            "status": row["status"],
            "resume_status": row["resume_status"],
        }

    def claim_qstash_run(self, run_id: str, generation: int) -> str:
        """Atomically claim one queued delivery; duplicate/stale messages become no-ops."""
        if self._redis_state is not None:
            return self._redis_state.claim_qstash_run(run_id, generation)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return "missing"
                if int(run.get("dispatch_generation", 0)) != generation:
                    return "superseded"
                state = run.get("status")
                if state != "queued":
                    return state
                run["status"] = "running"
                return "claimed"

        now = datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE workflow_runs SET status='running'
                   WHERE run_id=%s AND status='queued' AND dispatch_generation=%s
                   RETURNING run_id""",
                (run_id, generation),
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                    (run_id, "started", json.dumps({"dispatch_generation": generation}), now),
                )
                return "claimed"
            current = conn.execute(
                "SELECT status, dispatch_generation FROM workflow_runs WHERE run_id=%s",
                (run_id,),
            ).fetchone()
        if not current:
            return "missing"
        if int(current["dispatch_generation"] or 0) != generation:
            return "superseded"
        return str(current["status"])

    def save_paused_result(self, run_id: str, result: dict[str, Any]) -> bool:
        """Stage a completed inference result while keeping the run paused."""
        if self._redis_state is not None:
            return self._redis_state.save_paused_result(run_id, result)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or run.get("status") != "paused" or run.get("pending_result") is not None:
                    return False
                run["pending_result"] = copy.deepcopy(result)
                return True
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE workflow_runs SET pending_result_json=%s
                   WHERE run_id=%s AND status='paused' AND pending_result_json IS NULL
                   RETURNING run_id""",
                (_json_dumps(result), run_id),
            ).fetchone()
        return bool(row)

    def take_pending_result(self, run_id: str) -> dict[str, Any] | None:
        """Atomically take the result staged at a pause checkpoint."""
        if self._redis_state is not None:
            return self._redis_state.take_pending_result(run_id)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return None
                result = run.get("pending_result")
                run["pending_result"] = None
                return copy.deepcopy(result)
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE workflow_runs SET pending_result_json=NULL
                   WHERE run_id=%s AND pending_result_json IS NOT NULL
                   RETURNING pending_result_json""",
                (run_id,),
            ).fetchone()
        return row["pending_result_json"] if row else None

    def fail_run_if_active(self, result: dict[str, Any]) -> bool:
        """Persist a queue failure, staging it once when the run is user-paused."""
        if self._redis_state is not None:
            return self._redis_state.fail_run_if_active(result)
        run_id = result["run_id"]
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return False
                state = run.get("status")
                if state == "paused":
                    if run.get("pending_result") is not None:
                        return False
                    run["pending_result"] = copy.deepcopy(result)
                    return True
                if state not in {"queued", "running"}:
                    return False
                run.update({
                    "status": "failed",
                    "output": copy.deepcopy(result.get("output", {})),
                    "evidence": copy.deepcopy(result.get("evidence", [])),
                    "duration_ms": result.get("duration_ms", 0),
                    "created_at": result.get("created_at", run["created_at"]),
                    "usage": _normalize_usage(result.get("usage")),
                    "warnings": copy.deepcopy(result.get("warnings", [])),
                })
                return True
        now = datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE workflow_runs
                   SET pending_result_json=%s
                   WHERE run_id=%s AND status='paused' AND pending_result_json IS NULL
                   RETURNING run_id""",
                (_json_dumps(result), run_id),
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                    (run_id, "failure_staged", _json_dumps({"source": "queue_failure_callback"}), now),
                )
                return True
            row = conn.execute(
                """UPDATE workflow_runs
                   SET status='failed', output_json=%s, evidence_json=%s, duration_ms=%s,
                       usage_json=NULL, warnings_json=%s, error=%s
                   WHERE run_id=%s AND status IN ('queued','running')
                   RETURNING run_id""",
                (
                    _json_dumps(result.get("output", {})),
                    _json_dumps(result.get("evidence", [])),
                    result.get("duration_ms", 0),
                    _json_dumps(result.get("warnings", [])),
                    result.get("output", {}).get("error"),
                    run_id,
                ),
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                    (run_id, "failed", _json_dumps({"source": "queue_failure_callback"}), now),
                )
        return bool(row)

    def pause_run(self, run_id: str) -> bool:
        """Pause an active run at its next durable workflow boundary."""
        if self._redis_state is not None:
            return self._redis_state.pause_run(run_id)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or run.get("status") not in {"queued", "running"}:
                    return False
                run["resume_status"] = run["status"]
                run["status"] = "paused"
                return True

        now = datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row or row["status"] not in {"queued", "running"}:
                return False
            conn.execute(
                "UPDATE workflow_runs SET status='paused', resume_status=%s WHERE run_id=%s",
                (row["status"], run_id),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "paused", json.dumps({"resume_status": row["status"]}), now),
            )
        return True

    def resume_run(self, run_id: str) -> bool:
        """Resume a paused run, restoring its queued/running state."""
        if self._redis_state is not None:
            return self._redis_state.resume_run(run_id)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or run.get("status") != "paused":
                    return False
                run["status"] = run.pop("resume_status", None) or "queued"
                return True

        now = datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, resume_status FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row or row["status"] != "paused":
                return False
            restored_status = row["resume_status"] if row["resume_status"] in {"queued", "running"} else "queued"
            conn.execute(
                "UPDATE workflow_runs SET status=%s, resume_status=NULL WHERE run_id=%s",
                (restored_status, run_id),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "resumed", json.dumps({"status": restored_status}), now),
            )
        return True

    def stop_run(self, run_id: str) -> bool:
        """Mark an active run canceled; late worker results may not revive it."""
        if self._redis_state is not None:
            return self._redis_state.stop_run(run_id)
        stopped_output = {"error": "Workflow was stopped by the user."}
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run or run.get("status") not in {"queued", "running", "paused"}:
                    return False
                run["status"] = "cancelled"
                run["resume_status"] = None
                run["pending_result"] = None
                run["output"] = stopped_output
                run["warnings"] = ["Execution was canceled by the user."]
                return True

        now = datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row or row["status"] not in {"queued", "running", "paused"}:
                return False
            conn.execute(
                """UPDATE workflow_runs
                   SET status='cancelled', resume_status=NULL, pending_result_json=NULL,
                       output_json=%s, evidence_json='[]'::jsonb, warnings_json=%s,
                       duration_ms=0, usage_json=NULL, error=%s
                   WHERE run_id=%s""",
                (
                    _json_dumps(stopped_output),
                    _json_dumps(["Execution was canceled by the user."]),
                    stopped_output["error"],
                    run_id,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "cancelled", "{}", now),
            )
        return True

    def store_gateway_job_credential(
        self,
        run_id: str,
        encrypted_token: bytes,
        ttl_seconds: int,
    ) -> str:
        """Persist an encrypted short-lived bearer and return an opaque UUID.

        The raw bearer is never accepted by this repository method. Only the
        already-encrypted bytes are stored, and callers put only the returned
        UUID in the RQ job arguments.
        """
        if self._redis_state is not None:
            return self._redis_state.store_gateway_job_credential(run_id, encrypted_token, ttl_seconds)
        if self._use_memory:
            raise RuntimeError(
                "Queued Gateway sessions require persistent PostgreSQL storage."
            )
        if not encrypted_token:
            raise ValueError("Encrypted Gateway credential cannot be empty")
        if not 60 <= int(ttl_seconds) <= 1800:
            raise ValueError("Gateway credential TTL is outside the allowed range")

        credential_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=int(ttl_seconds))
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM gateway_job_credentials WHERE expires_at <= %s",
                (now,),
            )
            conn.execute(
                """INSERT INTO gateway_job_credentials
                   (credential_id, run_id, encrypted_token, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (credential_id, run_id, bytes(encrypted_token), now, expires_at),
            )
            conn.execute(
                """INSERT INTO audit_events(run_id,event,detail_json,created_at)
                   VALUES (%s,%s,%s,%s)""",
                (run_id, "gateway_credential_queued", "{}", now),
            )
        return credential_id

    def consume_gateway_job_credential(
        self,
        run_id: str,
        credential_id: str,
    ) -> bytes | None:
        """Atomically consume the encrypted credential for exactly one run."""
        if self._redis_state is not None:
            return self._redis_state.consume_gateway_job_credential(run_id, credential_id)
        if self._use_memory:
            raise RuntimeError(
                "Queued Gateway sessions require persistent PostgreSQL storage."
            )
        now = datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                """DELETE FROM gateway_job_credentials
                   WHERE credential_id=%s AND run_id=%s AND expires_at > %s
                   RETURNING encrypted_token""",
                (credential_id, run_id, now),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO audit_events(run_id,event,detail_json,created_at)
                       VALUES (%s,%s,%s,%s)""",
                    (run_id, "gateway_credential_consumed", "{}", now),
                )
                value = row["encrypted_token"]
                return bytes(value) if value is not None else None
            conn.execute(
                """DELETE FROM gateway_job_credentials
                   WHERE credential_id=%s AND run_id=%s AND expires_at <= %s""",
                (credential_id, run_id, now),
            )
        return None

    def delete_gateway_job_credential(
        self,
        run_id: str,
        credential_id: str,
    ) -> None:
        """Best-effort cleanup when RQ dispatch fails."""
        if self._redis_state is not None:
            return self._redis_state.delete_gateway_job_credential(run_id, credential_id)
        if self._use_memory:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM gateway_job_credentials WHERE credential_id=%s AND run_id=%s",
                (credential_id, run_id),
            )

    def mark_running(self, run_id: str) -> bool:
        """Atomically claim queued work; return False for paused or terminal runs."""
        if self._redis_state is not None:
            return self._redis_state.mark_running(run_id)
        now = datetime.now(UTC)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    return False
                status = run.get("status")
                if status != "queued":
                    return False
                run["status"] = "running"
                return True

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row:
                return False
            if row["status"] != "queued":
                return False
            changed = conn.execute(
                "UPDATE workflow_runs SET status='running' WHERE run_id=%s AND status='queued' RETURNING run_id",
                (run_id,),
            ).fetchone()
            if changed:
                conn.execute(
                    "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                    (run_id, "started", "{}", now),
                )
        return bool(changed)

    def save_result(self, result: dict[str, Any]) -> None:
        if self._redis_state is not None:
            return self._redis_state.save_result(result)
        if self._use_memory:
            with self._memory_lock:
                record = self._memory_runs.get(result["run_id"])
                if not record:
                    raise KeyError(f"Run not found: {result['run_id']}")
                # A canceled or paused state is authoritative. Late worker
                # completions must not resurrect a run that an operator controlled.
                if record.get("status") in {"cancelled", "paused", "failed", "completed", "completed_with_warnings", "awaiting_approval"}:
                    return
                record.update(
                    {
                        "workflow": result.get("workflow", record["workflow"]),
                        "status": result["status"],
                        "output": copy.deepcopy(result.get("output", {})),
                        "evidence": copy.deepcopy(result.get("evidence", [])),
                        "duration_ms": result.get("duration_ms", 0),
                        "created_at": result.get("created_at", record["created_at"]),
                        "usage": _normalize_usage(result.get("usage")),
                        "warnings": copy.deepcopy(result.get("warnings", [])),
                    }
                )
            return
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (result["run_id"],),
            ).fetchone()
            if not current or current["status"] not in {"queued", "running"}:
                return
            conn.execute(
                """UPDATE workflow_runs
                   SET status=%s, output_json=%s, evidence_json=%s, duration_ms=%s,
                       usage_json=%s, warnings_json=%s, error=%s
                   WHERE run_id=%s""",
                (
                    result["status"],
                    _json_dumps(result.get("output", {})),
                    _json_dumps(result.get("evidence", [])),
                    result.get("duration_ms", 0),
                    _json_dumps(_normalize_usage(result.get("usage"))) if result.get("usage") else None,
                    _json_dumps(result.get("warnings", [])),
                    result.get("output", {}).get("error") if result["status"] == "failed" else None,
                    result["run_id"],
                ),
            )
            now = datetime.now(UTC)
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (result["run_id"], result["status"], json.dumps({"duration_ms": result.get("duration_ms", 0)}), now),
            )

    def get_run(
        self,
        run_id: str,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        if self._redis_state is not None:
            return self._redis_state.get_run(run_id, owner_id=owner_id, include_all=include_all)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if run is None:
                    return None
                if not include_all and owner_id is not None and run.get("owner_id", "legacy") != owner_id:
                    return None
                return copy.deepcopy(run)
        query = "SELECT * FROM workflow_runs WHERE run_id=%s"
        params: tuple[Any, ...] = (run_id,)
        if not include_all and owner_id is not None:
            query += " AND owner_id=%s"
            params = (run_id, owner_id)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(
        self,
        limit: int = 20,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        if self._redis_state is not None:
            return self._redis_state.list_runs(limit, owner_id=owner_id, include_all=include_all)
        safe_limit = min(max(int(limit), 1), 100)
        if self._use_memory:
            with self._memory_lock:
                records = list(self._memory_runs.values())
                if not include_all and owner_id is not None:
                    records = [run for run in records if run.get("owner_id", "legacy") == owner_id]
                runs = sorted(records, key=lambda item: item["created_at"], reverse=True)[:safe_limit]
                return copy.deepcopy(runs)
        query = "SELECT * FROM workflow_runs"
        params: tuple[Any, ...]
        if not include_all and owner_id is not None:
            query += " WHERE owner_id=%s ORDER BY created_at DESC LIMIT %s"
            params = (owner_id, safe_limit)
        else:
            query += " ORDER BY created_at DESC LIMIT %s"
            params = (safe_limit,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    def clear_history(self) -> int:
        if self._redis_state is not None:
            return self._redis_state.clear_history()
        if self._use_memory:
            with self._memory_lock:
                active = [run for run in self._memory_runs.values() if run["status"] in ("queued", "running", "paused", "awaiting_approval")]
                if active:
                    raise ValueError("Cannot clear history while queued, running, or awaiting-approval workflows exist")
                doomed = [run_id for run_id, run in self._memory_runs.items() if run["status"] not in ("queued", "running", "paused", "awaiting_approval")]
                for run_id in doomed:
                    self._memory_runs.pop(run_id, None)
                    for approval_id, approval in list(self._memory_approvals.items()):
                        if approval["run_id"] == run_id:
                            self._memory_approvals.pop(approval_id, None)
                return len(doomed)
        with self._connect() as conn:
            active = conn.execute("SELECT COUNT(*) AS count FROM workflow_runs WHERE status IN ('queued', 'running', 'paused', 'awaiting_approval')").fetchone()
            if int(active["count"] or 0) > 0:
                raise ValueError("Cannot clear history while queued, running, or awaiting-approval workflows exist")
            result = conn.execute("DELETE FROM workflow_runs WHERE status NOT IN ('queued', 'running', 'paused', 'awaiting_approval') RETURNING run_id")
            return sum(1 for _ in result)

    def list_pending_approvals(
        self,
        limit: int = 50,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        """Return only this owner's pending approvals unless explicitly privileged."""
        if self._redis_state is not None:
            return self._redis_state.list_pending_approvals(limit, owner_id=owner_id, include_all=include_all)
        safe_limit = min(max(int(limit), 1), 100)
        if self._use_memory:
            with self._memory_lock:
                runs = [
                    copy.deepcopy(run)
                    for run in self._memory_runs.values()
                    if run.get("status") == "awaiting_approval"
                    and isinstance(run.get("output", {}).get("approval"), dict)
                    and (
                        include_all
                        or owner_id is None
                        or run.get("owner_id", "legacy") == owner_id
                    )
                ]
            runs.sort(key=lambda item: item["created_at"], reverse=True)
        else:
            query = """SELECT * FROM workflow_runs
                       WHERE status='awaiting_approval'
                         AND output_json ? 'approval'"""
            params: tuple[Any, ...]
            if not include_all and owner_id is not None:
                query += " AND owner_id=%s"
                params = (owner_id, safe_limit)
            else:
                params = (safe_limit,)
            query += " ORDER BY created_at DESC LIMIT %s"
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
            runs = [self._row_to_run(row) for row in rows]
        return [
            {
                "run_id": run["run_id"],
                "workflow": run["workflow"],
                "status": run["status"],
                "created_at": run["created_at"],
                "approval": run.get("output", {}).get("approval", {}),
                "output": run.get("output", {}),
            }
            for run in runs[:safe_limit]
        ]

    def create_approval(self, run_id: str, decision: str, reviewer: str, note: str) -> dict[str, Any]:
        """Record one reviewer decision and atomically close the pending gate."""
        if self._redis_state is not None:
            return self._redis_state.create_approval(run_id, decision, reviewer, note)
        if decision not in {"approved", "rejected"}:
            raise ValueError("Approval decision must be approved or rejected")
        approval_id = str(uuid.uuid4())
        decided_at = datetime.now(UTC)
        approval = {
            "approval_id": approval_id,
            "run_id": run_id,
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
            "decided_at": decided_at,
        }
        next_status = "approved" if decision == "approved" else "rejected"
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if not run:
                    raise KeyError("Run not found")
                if run.get("status") != "awaiting_approval":
                    raise ValueError("This run is no longer awaiting approval")
                output = run.setdefault("output", {})
                request = output.get("approval")
                if not isinstance(request, dict) or request.get("status") != "pending":
                    raise ValueError("No pending approval exists for this run")
                request.update({
                    "status": next_status,
                    "decision": decision,
                    "reviewer": reviewer,
                    "note": note,
                    "decided_at": decided_at.isoformat(),
                    "execution_status": "not_configured",
                    "execution_note": (
                        "Approval recorded. No external action was performed because no publishing or sending connector is configured."
                    ),
                })
                run["status"] = next_status
                self._memory_approvals[approval_id] = approval
            return copy.deepcopy(approval)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, output_json FROM workflow_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not row:
                raise KeyError("Run not found")
            if row["status"] != "awaiting_approval":
                raise ValueError("This run is no longer awaiting approval")
            output = row["output_json"] or {}
            request = output.get("approval")
            if not isinstance(request, dict) or request.get("status") != "pending":
                raise ValueError("No pending approval exists for this run")
            request.update({
                "status": next_status,
                "decision": decision,
                "reviewer": reviewer,
                "note": note,
                "decided_at": decided_at.isoformat(),
                "execution_status": "not_configured",
                "execution_note": (
                    "Approval recorded. No external action was performed because no publishing or sending connector is configured."
                ),
            })
            conn.execute(
                "UPDATE workflow_runs SET status=%s, output_json=%s WHERE run_id=%s",
                (next_status, _json_dumps(output), run_id),
            )
            conn.execute(
                "INSERT INTO approvals VALUES (%s,%s,%s,%s,%s,%s)",
                (approval_id, run_id, decision, reviewer, note, decided_at),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "human_approval", _json_dumps({"approval_id": approval_id, "decision": decision, "reviewer": reviewer}), decided_at),
            )
        return approval

    def metrics(self) -> dict[str, int]:
        if self._redis_state is not None:
            return self._redis_state.metrics()
        if self._use_memory:
            with self._memory_lock:
                runs = list(self._memory_runs.values())
                approvals = list(self._memory_approvals.values())
            return {
                "workflow_runs_total": len(runs),
                "workflow_runs_completed": sum(1 for run in runs if run["status"] in ("completed", "completed_with_warnings")),
                "workflow_runs_failed": sum(1 for run in runs if run["status"] == "failed"),
                "workflow_runs_queued": sum(1 for run in runs if run["status"] == "queued"),
                "workflow_runs_running": sum(1 for run in runs if run["status"] == "running"),
                "workflow_approvals_pending": sum(1 for run in runs if run["status"] == "awaiting_approval"),
                "workflow_approvals_total": len(approvals),
                "workflow_approvals_approved": sum(1 for item in approvals if item["decision"] == "approved"),
            }
        with self._connect() as conn:
            runs = conn.execute(
                """SELECT COUNT(*) AS total,
                          COUNT(*) FILTER (WHERE status IN ('completed','completed_with_warnings')) AS completed,
                          COUNT(*) FILTER (WHERE status='failed') AS failed,
                          COUNT(*) FILTER (WHERE status='queued') AS queued,
                          COUNT(*) FILTER (WHERE status='running') AS running
                          ,COUNT(*) FILTER (WHERE status='awaiting_approval') AS pending
                   FROM workflow_runs"""
            ).fetchone()
            approvals = conn.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE decision='approved') AS approved FROM approvals").fetchone()
        return {
            "workflow_runs_total": int(runs["total"] or 0),
            "workflow_runs_completed": int(runs["completed"] or 0),
            "workflow_runs_failed": int(runs["failed"] or 0),
            "workflow_runs_queued": int(runs["queued"] or 0),
            "workflow_runs_running": int(runs["running"] or 0),
            "workflow_approvals_pending": int(runs["pending"] or 0),
            "workflow_approvals_total": int(approvals["total"] or 0),
            "workflow_approvals_approved": int(approvals["approved"] or 0),
        }

    @staticmethod
    def _row_to_run(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(row["run_id"]),
            "owner_id": row.get("owner_id", "legacy"),
            "workflow": row["workflow"],
            "status": row["status"],
            "output": row["output_json"] or {},
            "evidence": row["evidence_json"] or [],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "usage": _normalize_usage(row["usage_json"]),
            "warnings": row["warnings_json"] or [],
        }
