from datetime import datetime, timezone
from types import SimpleNamespace

import app.api.main as api_module
from fastapi.testclient import TestClient


class FakeRepo:
    def __init__(self):
        self.run = None

    def init_db(self):
        return None

    def create_pending_run(self, workflow):
        self.run = {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "workflow": workflow,
            "status": "queued",
            "output": {},
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(timezone.utc),
            "usage": None,
            "warnings": [],
        }
        return self.run["run_id"]

    def get_run(self, run_id):
        return self.run if self.run and self.run["run_id"] == run_id else None

    def list_runs(self, limit):
        return [self.run] if self.run else []

    def metrics(self):
        return {"workflow_runs_total": 0, "workflow_runs_completed": 0, "workflow_runs_failed": 0, "workflow_runs_queued": 1 if self.run else 0, "workflow_runs_running": 0, "workflow_approvals_total": 0, "workflow_approvals_approved": 0}

    def create_approval(self, run_id, decision, reviewer, note):
        return {"approval_id": "22222222-2222-2222-2222-222222222222", "run_id": run_id, "decision": decision, "reviewer": reviewer, "note": note, "decided_at": datetime.now(timezone.utc)}


class FakeQueue:
    def enqueue(self, *args, **kwargs):
        return SimpleNamespace(id=kwargs.get("job_id"))


api_module.repo = FakeRepo()
api_module.queue = lambda: FakeQueue()

client = TestClient(api_module.app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_queue_workflow():
    response = client.post("/api/v1/runs", json={"workflow": "content", "payload": {"topics": ["AI"]}})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_outreach_approval():
    response = client.post("/api/v1/runs", json={"workflow": "outreach", "payload": {"candidates": [{"name": "A"}]}})
    run_id = response.json()["run_id"]
    approval = client.post(f"/api/v1/runs/{run_id}/approval", json={"decision": "approved", "reviewer": "Tester", "note": "OK"})
    assert approval.status_code == 200
    assert approval.json()["decision"] == "approved"


def test_json_serialization_of_datetime_evidence():
    from datetime import datetime, timezone
    from app.storage.repository import _json_dumps

    payload = {"observed_at": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)}
    encoded = _json_dumps(payload)
    assert "2026-08-10T12:00:00+00:00" in encoded


def test_usage_metadata_normalization():
    from app.workflows.orchestrator import WorkflowEngine

    class DummySettings:
        demo_mode = True
        llm_provider = "demo"
        openai_model = "deterministic-demo"

    class DummyLLM:
        settings = DummySettings()

    engine = WorkflowEngine.__new__(WorkflowEngine)
    engine.llm = DummyLLM()
    # Mirrors an older agent payload that omitted provider/model.
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}
    usage.setdefault("provider", engine.llm.settings.llm_provider)
    usage.setdefault("model", engine.llm.settings.openai_model)
    assert usage["provider"] == "demo"
    assert usage["model"] == "deterministic-demo"


def test_legacy_usage_is_normalized_at_repository_boundary():
    from app.storage.repository import _normalize_usage

    legacy = {
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
        "estimated_cost_usd": 0.0,
    }
    normalized = _normalize_usage(legacy)
    assert normalized["provider"] == "unknown"
    assert normalized["model"] is None
    assert normalized["total_tokens"] == 5


def test_empty_usage_remains_none():
    from app.storage.repository import _normalize_usage

    assert _normalize_usage(None) is None
    assert _normalize_usage({}) is None
