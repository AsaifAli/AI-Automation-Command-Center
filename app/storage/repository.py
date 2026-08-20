import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

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
    return normalized


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id UUID PRIMARY KEY,
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
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS output_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS duration_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS usage_json JSONB;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS error TEXT;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_created_at ON workflow_runs(created_at DESC);
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
"""


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _connect(self):
        return psycopg.connect(self.settings.database_url, row_factory=dict_row)

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(SCHEMA)

    def create_pending_run(self, workflow: str) -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workflow_runs(run_id,workflow,status,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, workflow, "queued", now),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "queued", json.dumps({"workflow": workflow}), now),
            )
        return run_id

    def mark_running(self, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("UPDATE workflow_runs SET status='running' WHERE run_id=%s", (run_id,))
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "started", "{}", now),
            )

    def save_result(self, result: dict[str, Any]) -> None:
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
            now = datetime.now(timezone.utc)
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (result["run_id"], result["status"], json.dumps({"duration_ms": result.get("duration_ms", 0)}), now),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
        return [self._row_to_run(row) for row in rows]

    def clear_history(self) -> int:
        """Delete completed/failed history while protecting active worker jobs."""
        with self._connect() as conn:
            active = conn.execute(
                "SELECT COUNT(*) AS count FROM workflow_runs WHERE status IN ('queued', 'running')"
            ).fetchone()
            if int(active["count"] or 0) > 0:
                raise ValueError("Cannot clear history while queued or running workflows exist")
            result = conn.execute(
                "DELETE FROM workflow_runs WHERE status NOT IN ('queued', 'running') RETURNING run_id"
            )
            return sum(1 for _ in result)

    def create_approval(self, run_id: str, decision: str, reviewer: str, note: str) -> dict[str, Any]:
        approval_id = str(uuid.uuid4())
        decided_at = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approvals VALUES (%s,%s,%s,%s,%s,%s)",
                (approval_id, run_id, decision, reviewer, note, decided_at),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,event,detail_json,created_at) VALUES (%s,%s,%s,%s)",
                (run_id, "human_approval", json.dumps({"approval_id": approval_id, "decision": decision, "reviewer": reviewer}), decided_at),
            )
        return {"approval_id": approval_id, "run_id": run_id, "decision": decision, "reviewer": reviewer, "note": note, "decided_at": decided_at}

    def metrics(self) -> dict[str, int]:
        with self._connect() as conn:
            runs = conn.execute(
                """SELECT COUNT(*) AS total,
                          COUNT(*) FILTER (WHERE status IN ('completed','completed_with_warnings')) AS completed,
                          COUNT(*) FILTER (WHERE status='failed') AS failed,
                          COUNT(*) FILTER (WHERE status='queued') AS queued,
                          COUNT(*) FILTER (WHERE status='running') AS running
                   FROM workflow_runs"""
            ).fetchone()
            approvals = conn.execute(
                "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE decision='approved') AS approved FROM approvals"
            ).fetchone()
        return {
            "workflow_runs_total": int(runs["total"] or 0),
            "workflow_runs_completed": int(runs["completed"] or 0),
            "workflow_runs_failed": int(runs["failed"] or 0),
            "workflow_runs_queued": int(runs["queued"] or 0),
            "workflow_runs_running": int(runs["running"] or 0),
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
