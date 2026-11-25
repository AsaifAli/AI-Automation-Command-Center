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
"""


class Repository:
    """Workflow state repository with optional PostgreSQL or ephemeral memory."""

    _memory_runs: ClassVar[dict[str, dict[str, Any]]] = {}
    _memory_approvals: ClassVar[dict[str, dict[str, Any]]] = {}
    _memory_lock: ClassVar[Any] = threading.RLock()

    def __init__(self, settings: Settings):
        self.settings = settings
        self._use_memory = not bool(settings.database_url.strip())

    def _connect(self):
        if self._use_memory:
            raise RuntimeError("In-memory repository does not use a database connection")
        return psycopg.connect(self.settings.database_url, row_factory=dict_row)

    def init_db(self) -> None:
        if self._use_memory:
            return
        with self._connect() as conn:
            conn.execute(SCHEMA)
            conn.execute(
                "DELETE FROM gateway_job_credentials WHERE expires_at <= %s",
                (datetime.now(UTC),),
            )

    def create_pending_run(self, workflow: str, owner_id: str = "legacy") -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = {
            "run_id": run_id,
            "owner_id": owner_id,
            "workflow": workflow,
            "status": "queued",
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
        if self._use_memory:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM gateway_job_credentials WHERE credential_id=%s AND run_id=%s",
                (credential_id, run_id),
            )

    def mark_running(self, run_id: str) -> None:
        now = datetime.now(UTC)
        if self._use_memory:
            with self._memory_lock:
                run = self._memory_runs.get(run_id)
                if run:
                    run["status"] = "running"
            return
        with self._connect() as conn:
            conn.execute("UPDATE workflow_runs SET status='running' WHERE run_id=%s", (run_id,))
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "started", "{}", now),
            )

    def save_result(self, result: dict[str, Any]) -> None:
        if self._use_memory:
            with self._memory_lock:
                record = self._memory_runs.get(result["run_id"])
                if not record:
                    raise KeyError(f"Run not found: {result['run_id']}")
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
        if self._use_memory:
            with self._memory_lock:
                active = [run for run in self._memory_runs.values() if run["status"] in ("queued", "running", "awaiting_approval")]
                if active:
                    raise ValueError("Cannot clear history while queued, running, or awaiting-approval workflows exist")
                doomed = [run_id for run_id, run in self._memory_runs.items() if run["status"] not in ("queued", "running", "awaiting_approval")]
                for run_id in doomed:
                    self._memory_runs.pop(run_id, None)
                    for approval_id, approval in list(self._memory_approvals.items()):
                        if approval["run_id"] == run_id:
                            self._memory_approvals.pop(approval_id, None)
                return len(doomed)
        with self._connect() as conn:
            active = conn.execute("SELECT COUNT(*) AS count FROM workflow_runs WHERE status IN ('queued', 'running', 'awaiting_approval')").fetchone()
            if int(active["count"] or 0) > 0:
                raise ValueError("Cannot clear history while queued, running, or awaiting-approval workflows exist")
            result = conn.execute("DELETE FROM workflow_runs WHERE status NOT IN ('queued', 'running', 'awaiting_approval') RETURNING run_id")
            return sum(1 for _ in result)

    def list_pending_approvals(
        self,
        limit: int = 50,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        """Return only this owner's pending approvals unless explicitly privileged."""
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
            "workflow": row["workflow"],
            "status": row["status"],
            "output": row["output_json"] or {},
            "evidence": row["evidence_json"] or [],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "usage": _normalize_usage(row["usage_json"]),
            "warnings": row["warnings_json"] or [],
        }
