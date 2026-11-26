from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from redis.exceptions import WatchError

from app.core.config import Settings


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
        separators=(",", ":"),
    )


class RedisStateStore:
    """Persistent, owner-scoped workflow state using an existing Redis database.

    All keys are namespaced so this store can safely share a Redis database with
    unrelated applications. Run mutations use WATCH/MULTI to serialize control
    operations with worker completions and queue deliveries.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = Redis.from_url(
            settings.redis_url,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=30,
        )
        self.prefix = settings.redis_key_prefix.strip(":") or "aiac"

    def _key(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    def _run_key(self, run_id: str) -> str:
        return self._key(f"run:{run_id}")

    def _owner_index(self, owner_id: str) -> str:
        owner_hash = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        return self._key(f"owner-runs:{owner_hash}")

    @staticmethod
    def _decode(raw: bytes | str | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        value = json.loads(raw)
        created_at = value.get("created_at")
        if isinstance(created_at, str):
            try:
                value["created_at"] = datetime.fromisoformat(created_at)
            except ValueError:
                pass
        return value

    @staticmethod
    def _created_score(run: dict[str, Any]) -> float:
        created_at = run.get("created_at")
        if isinstance(created_at, datetime):
            return created_at.timestamp()
        if isinstance(created_at, str):
            try:
                return datetime.fromisoformat(created_at).timestamp()
            except ValueError:
                pass
        return datetime.now(UTC).timestamp()

    def _index_run(self, pipeline, run: dict[str, Any]) -> None:
        run_id = run["run_id"]
        score = self._created_score(run)
        pipeline.zadd(self._key("runs"), {run_id: score})
        pipeline.zadd(self._owner_index(run.get("owner_id", "legacy")), {run_id: score})
        if run.get("status") == "awaiting_approval":
            pipeline.zadd(self._key("pending-approvals"), {run_id: score})
        else:
            pipeline.zrem(self._key("pending-approvals"), run_id)

    def _save_run(self, run: dict[str, Any]) -> None:
        with self.client.pipeline(transaction=True) as pipe:
            pipe.set(self._run_key(run["run_id"]), _json_dumps(run))
            self._index_run(pipe, run)
            pipe.execute()

    def _load_run(self, run_id: str) -> dict[str, Any] | None:
        return self._decode(self.client.get(self._run_key(run_id)))

    def _mutate_run(self, run_id: str, mutate: Callable[[dict[str, Any]], Any]) -> Any:
        key = self._run_key(run_id)
        while True:
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    run = self._decode(pipe.get(key))
                    if run is None:
                        pipe.unwatch()
                        return None
                    result = mutate(run)
                    pipe.multi()
                    pipe.set(key, _json_dumps(run))
                    self._index_run(pipe, run)
                    pipe.execute()
                    return result
                except WatchError:
                    continue

    def _append_audit(self, run_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        item = {
            "event": event,
            "detail": detail or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.client.lpush(self._key(f"audit:{run_id}"), _json_dumps(item))

    def _read_runs(self, *, limit: int, owner_id: str | None = None) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 10000)
        index = self._owner_index(owner_id) if owner_id is not None else self._key("runs")
        run_ids = self.client.zrevrange(index, 0, safe_limit - 1)
        if not run_ids:
            return []
        keys = [self._run_key(item.decode() if isinstance(item, bytes) else str(item)) for item in run_ids]
        raw_runs = self.client.mget(keys)
        return [run for raw in raw_runs if (run := self._decode(raw)) is not None]

    def ping(self) -> bool:
        return bool(self.client.ping())

    def init_db(self) -> None:
        self.ping()

    def create_pending_run(self, workflow: str, owner_id: str = "legacy") -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        self._save_run({
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
        })
        self._append_audit(run_id, "queued", {"workflow": workflow})
        return run_id

    def ensure_pending_run(self, run_id: str, workflow: str, owner_id: str) -> None:
        try:
            normalized = str(uuid.UUID(str(run_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Workflow run identifier is invalid.") from exc
        if normalized != str(run_id):
            raise ValueError("Workflow run identifier must be a canonical UUID.")
        if not isinstance(workflow, str) or not workflow.strip():
            raise ValueError("Workflow name is required.")
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("Workflow owner scope is invalid.")

        existing = self._load_run(normalized)
        if existing:
            if existing.get("workflow") != workflow or existing.get("owner_id") != owner_id:
                raise ValueError("Workflow run scope does not match the existing run.")
            return
        self._save_run({
            "run_id": normalized,
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
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        })
        self._append_audit(normalized, "queued", {"workflow": workflow})

    def _prepare_dispatch(self, run_id: str, payload: dict[str, Any], field: str) -> int:
        def update(run):
            if run.get("status") != "queued":
                raise ValueError("Only queued runs can be dispatched.")
            generation = int(run.get("dispatch_generation", 0)) + 1
            payload["dispatch_generation"] = generation
            run["dispatch_generation"] = generation
            run["dispatch_payload"] = copy.deepcopy(payload)
            run[field] = None
            return generation

        result = self._mutate_run(run_id, update)
        if result is None:
            raise ValueError("Only queued runs can be dispatched.")
        return int(result)

    def prepare_qstash_dispatch(self, run_id: str, payload: dict[str, Any]) -> int:
        return self._prepare_dispatch(run_id, payload, "qstash_message_id")

    def prepare_rq_dispatch(self, run_id: str, payload: dict[str, Any]) -> int:
        return self._prepare_dispatch(run_id, payload, "rq_job_id")

    def set_rq_job_id(self, run_id: str, generation: int, job_id: str) -> bool:
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 256:
            raise ValueError("RQ job identifier is invalid.")
        result = self._mutate_run(
            run_id,
            lambda run: (
                run.__setitem__("rq_job_id", job_id) or True
                if int(run.get("dispatch_generation", 0)) == generation
                else False
            ),
        )
        return bool(result)

    def claim_rq_run(self, run_id: str, generation: int, job_id: str) -> bool:
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            return False
        if not isinstance(job_id, str) or not job_id.strip():
            return False

        def update(run):
            if int(run.get("dispatch_generation", 0)) != generation or run.get("rq_job_id") != job_id:
                return False
            if run.get("status") == "queued":
                run["status"] = "running"
                return True
            # RQ can replay the same persisted job after a worker restart.
            return run.get("status") == "running"

        result = self._mutate_run(run_id, update)
        if result is True:
            self._append_audit(run_id, "started", {"queue": "rq", "generation": generation})
        return bool(result)

    def get_rq_dispatch(self, run_id: str) -> dict[str, Any] | None:
        run = self._load_run(run_id)
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

    def restore_qstash_dispatch(self, run_id: str, payload: dict[str, Any]) -> None:
        generation = payload.get("dispatch_generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError("QStash dispatch generation is invalid.")

        def update(run):
            current = int(run.get("dispatch_generation", 0))
            if generation > current:
                run["dispatch_generation"] = generation
                run["dispatch_payload"] = copy.deepcopy(payload)
            return True

        if self._mutate_run(run_id, update) is None:
            raise KeyError(f"Run not found: {run_id}")

    def set_qstash_message_id(self, run_id: str, generation: int, message_id: str) -> bool:
        if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 256:
            raise ValueError("QStash message identifier is invalid.")
        result = self._mutate_run(
            run_id,
            lambda run: (
                run.__setitem__("qstash_message_id", message_id) or True
                if int(run.get("dispatch_generation", 0)) == generation
                else False
            ),
        )
        return bool(result)

    def get_qstash_dispatch(self, run_id: str) -> dict[str, Any] | None:
        run = self._load_run(run_id)
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

    def claim_qstash_run(self, run_id: str, generation: int) -> str:
        def update(run):
            if int(run.get("dispatch_generation", 0)) != generation:
                return "superseded"
            if run.get("status") != "queued":
                return run.get("status", "missing")
            run["status"] = "running"
            return "claimed"

        result = self._mutate_run(run_id, update)
        if result is None:
            return "missing"
        if result == "claimed":
            self._append_audit(run_id, "started", {"dispatch_generation": generation})
        return str(result)

    def save_paused_result(self, run_id: str, result: dict[str, Any]) -> bool:
        def update(run):
            if run.get("status") != "paused" or run.get("pending_result") is not None:
                return False
            run["pending_result"] = copy.deepcopy(result)
            return True

        return bool(self._mutate_run(run_id, update))

    def take_pending_result(self, run_id: str) -> dict[str, Any] | None:
        def update(run):
            pending = copy.deepcopy(run.get("pending_result"))
            run["pending_result"] = None
            return pending

        return self._mutate_run(run_id, update)

    def fail_run_if_active(self, result: dict[str, Any]) -> bool:
        run_id = result["run_id"]

        def update(run):
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
                "usage": copy.deepcopy(result.get("usage")),
                "warnings": copy.deepcopy(result.get("warnings", [])),
            })
            return True

        changed = bool(self._mutate_run(run_id, update))
        if changed:
            self._append_audit(run_id, "failed", {"source": "worker_failure"})
        return changed

    def pause_run(self, run_id: str) -> bool:
        def update(run):
            if run.get("status") not in {"queued", "running"}:
                return False
            run["resume_status"] = run["status"]
            run["status"] = "paused"
            return True

        changed = bool(self._mutate_run(run_id, update))
        if changed:
            self._append_audit(run_id, "paused")
        return changed

    def resume_run(self, run_id: str) -> bool:
        def update(run):
            if run.get("status") != "paused":
                return False
            restored = run.get("resume_status")
            run["status"] = restored if restored in {"queued", "running"} else "queued"
            run["resume_status"] = None
            return run["status"]

        restored = self._mutate_run(run_id, update)
        if not restored:
            return False
        self._append_audit(run_id, "resumed", {"status": restored})
        return True

    def stop_run(self, run_id: str) -> bool:
        stopped_output = {"error": "Workflow was stopped by the user."}

        def update(run):
            if run.get("status") not in {"queued", "running", "paused"}:
                return False
            run.update({
                "status": "cancelled",
                "resume_status": None,
                "output": stopped_output,
                "evidence": [],
                "duration_ms": 0,
                "usage": None,
                "warnings": ["Execution was canceled by the user."],
                "pending_result": None,
            })
            return True

        changed = bool(self._mutate_run(run_id, update))
        if changed:
            self._append_audit(run_id, "cancelled")
        return changed

    def store_gateway_job_credential(
        self,
        run_id: str,
        encrypted_token: bytes,
        ttl_seconds: int,
    ) -> str:
        if not encrypted_token:
            raise ValueError("Encrypted Gateway credential cannot be empty")
        if not 60 <= int(ttl_seconds) <= 1800:
            raise ValueError("Gateway credential TTL is outside the allowed range")
        if not self._load_run(run_id):
            raise KeyError(f"Run not found: {run_id}")
        credential_id = str(uuid.uuid4())
        self.client.set(
            self._key(f"gateway-credential:{run_id}:{credential_id}"),
            bytes(encrypted_token),
            ex=int(ttl_seconds),
        )
        self._append_audit(run_id, "gateway_credential_queued")
        return credential_id

    def consume_gateway_job_credential(self, run_id: str, credential_id: str) -> bytes | None:
        if not credential_id or len(credential_id) > 256:
            return None
        value = self.client.getdel(self._key(f"gateway-credential:{run_id}:{credential_id}"))
        if value is not None:
            self._append_audit(run_id, "gateway_credential_consumed")
            return bytes(value)
        return None

    def delete_gateway_job_credential(self, run_id: str, credential_id: str) -> None:
        self.client.delete(self._key(f"gateway-credential:{run_id}:{credential_id}"))

    def mark_running(self, run_id: str) -> bool:
        def update(run):
            if run.get("status") != "queued":
                return False
            run["status"] = "running"
            return True

        changed = bool(self._mutate_run(run_id, update))
        if changed:
            self._append_audit(run_id, "started")
        return changed

    def save_result(self, result: dict[str, Any]) -> None:
        run_id = result["run_id"]
        terminal = {
            "cancelled", "paused", "failed", "completed",
            "completed_with_warnings", "awaiting_approval",
        }

        def update(run):
            if run.get("status") not in {"queued", "running"} or run.get("status") in terminal:
                return False
            run.update({
                "workflow": result.get("workflow", run["workflow"]),
                "status": result["status"],
                "output": copy.deepcopy(result.get("output", {})),
                "evidence": copy.deepcopy(result.get("evidence", [])),
                "duration_ms": result.get("duration_ms", 0),
                "created_at": result.get("created_at", run["created_at"]),
                "usage": copy.deepcopy(result.get("usage")),
                "warnings": copy.deepcopy(result.get("warnings", [])),
            })
            return True

        changed = self._mutate_run(run_id, update)
        if changed is None:
            raise KeyError(f"Run not found: {run_id}")
        if changed:
            self._append_audit(run_id, result["status"], {"duration_ms": result.get("duration_ms", 0)})

    def get_run(
        self,
        run_id: str,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        run = self._load_run(run_id)
        if not run:
            return None
        if not include_all and owner_id is not None and run.get("owner_id", "legacy") != owner_id:
            return None
        return run

    def list_runs(
        self,
        limit: int = 20,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 100)
        return self._read_runs(limit=safe_limit, owner_id=None if include_all else owner_id)

    def clear_history(self) -> int:
        runs = self._read_runs(limit=10000)
        if any(run.get("status") in {"queued", "running", "paused", "awaiting_approval"} for run in runs):
            raise ValueError("Cannot clear history while queued, running, or awaiting-approval workflows exist")
        if not runs:
            return 0
        keys = [self._run_key(run["run_id"]) for run in runs]
        keys.extend(self._key(f"audit:{run['run_id']}") for run in runs)
        keys.extend(self._owner_index(run.get("owner_id", "legacy")) for run in runs)
        for run in runs:
            pattern = self._key(f"gateway-credential:{run['run_id']}:*")
            keys.extend(self.client.scan_iter(match=pattern))
        self.client.delete(*keys, self._key("runs"), self._key("pending-approvals"), self._key("approvals"))
        return len(runs)

    def list_pending_approvals(
        self,
        limit: int = 50,
        *,
        owner_id: str | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 100)
        runs = self._read_runs(
            limit=10000,
            owner_id=None if include_all else owner_id,
        )
        pending = [
            run for run in runs
            if run.get("status") == "awaiting_approval"
            and isinstance(run.get("output", {}).get("approval"), dict)
        ]
        return [
            {
                "run_id": run["run_id"],
                "workflow": run["workflow"],
                "status": run["status"],
                "created_at": run["created_at"],
                "approval": run.get("output", {}).get("approval", {}),
                "output": run.get("output", {}),
            }
            for run in pending[:safe_limit]
        ]

    def create_approval(self, run_id: str, decision: str, reviewer: str, note: str) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Approval decision must be approved or rejected")
        approval_id = str(uuid.uuid4())
        decided_at = datetime.now(UTC)
        next_status = "approved" if decision == "approved" else "rejected"
        approval = {
            "approval_id": approval_id,
            "run_id": run_id,
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
            "decided_at": decided_at,
        }

        def update(run):
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
            return True

        updated = self._mutate_run(run_id, update)
        if updated is None:
            raise KeyError("Run not found")
        if not updated:
            raise ValueError("This run is no longer awaiting approval")
        self.client.hset(self._key("approvals"), approval_id, _json_dumps(approval))
        self._append_audit(run_id, "human_approval", {"approval_id": approval_id, "decision": decision})
        return approval

    def metrics(self) -> dict[str, int]:
        runs = self._read_runs(limit=10000)
        approvals = self.client.hvals(self._key("approvals"))
        approval_records = [self._decode_approval(raw) for raw in approvals]
        return {
            "workflow_runs_total": len(runs),
            "workflow_runs_completed": sum(1 for run in runs if run.get("status") in {"completed", "completed_with_warnings"}),
            "workflow_runs_failed": sum(1 for run in runs if run.get("status") == "failed"),
            "workflow_runs_queued": sum(1 for run in runs if run.get("status") == "queued"),
            "workflow_runs_running": sum(1 for run in runs if run.get("status") == "running"),
            "workflow_approvals_pending": sum(1 for run in runs if run.get("status") == "awaiting_approval"),
            "workflow_approvals_total": len(approval_records),
            "workflow_approvals_approved": sum(1 for item in approval_records if item.get("decision") == "approved"),
        }

    @staticmethod
    def _decode_approval(raw: bytes | str) -> dict[str, Any]:
        value = json.loads(raw)
        decided_at = value.get("decided_at")
        if isinstance(decided_at, str):
            try:
                value["decided_at"] = datetime.fromisoformat(decided_at)
            except ValueError:
                pass
        return value
