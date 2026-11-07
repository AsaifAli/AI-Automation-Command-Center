
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api.main as api_module
import app.core.auth as auth_module
from app.core.config import Settings


class FakeRepo:
    def __init__(self):
        self.runs = {}
        self.approvals = []

    def create_pending_run(self, workflow):
        run_id = f"run-{len(self.runs) + 1}"

        self.runs[run_id] = {
            "run_id": run_id,
            "workflow": workflow,
            "status": "queued",
            "output": {},
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(timezone.utc),
            "usage": None,
            "warnings": [],
        }

        return run_id

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def list_runs(self, limit):
        return list(self.runs.values())[:limit]

    def clear_history(self):
        count = len(self.runs)
        self.runs.clear()
        self.approvals.clear()
        return count

    def metrics(self):
        return {
            "workflow_runs_total": len(self.runs),
            "workflow_runs_completed": 0,
            "workflow_runs_failed": 0,
            "workflow_runs_queued": len(self.runs),
            "workflow_runs_running": 0,
            "workflow_approvals_total": len(self.approvals),
            "workflow_approvals_approved": sum(
                item["decision"] == "approved"
                for item in self.approvals
            ),
        }

    def create_approval(
        self,
        run_id,
        decision,
        reviewer,
        note,
    ):
        result = {
            "approval_id": f"approval-{len(self.approvals) + 1}",
            "run_id": run_id,
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
            "decided_at": datetime.now(timezone.utc),
        }

        self.approvals.append(result)
        return result


class FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(
            id=kwargs.get("job_id")
        )


def test_api_enforces_roles_and_reviewer_identity(monkeypatch):
    settings = Settings(
        api_auth_enabled=True,
        api_viewer_keys="viewer-key",
        api_operator_keys="operator-key",
        api_reviewer_keys="reviewer-key",
        api_admin_keys="admin-key",
        api_reviewer_name="Authenticated Reviewer",
    )

    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: settings,
    )

    monkeypatch.setattr(
        api_module.settings,
        "inline_execution",
        False,
    )

    fake_repo = FakeRepo()
    fake_queue = FakeQueue()

    monkeypatch.setattr(
        api_module,
        "repo",
        fake_repo,
    )

    monkeypatch.setattr(
        api_module,
        "queue",
        lambda: fake_queue,
    )

    client = TestClient(api_module.app)

    # Missing key.
    assert client.get(
        "/api/v1/workflows"
    ).status_code == 401

    # Viewer can read.
    assert client.get(
        "/api/v1/workflows",
        headers={"X-API-Key": "viewer-key"},
    ).status_code == 200

    # Viewer cannot execute.
    viewer_run = client.post(
        "/api/v1/runs",
        headers={"X-API-Key": "viewer-key"},
        json={
            "workflow": "content",
            "payload": {},
        },
    )

    assert viewer_run.status_code == 403

    # Operator can execute.
    created = client.post(
        "/api/v1/runs",
        headers={"X-API-Key": "operator-key"},
        json={
            "workflow": "outreach",
            "payload": {},
        },
    )

    assert created.status_code == 202

    run_id = created.json()["run_id"]

    # Operator cannot approve.
    forbidden_approval = client.post(
        f"/api/v1/runs/{run_id}/approval",
        headers={"X-API-Key": "operator-key"},
        json={
            "decision": "approved",
            "reviewer": "Forged Reviewer",
        },
    )

    assert forbidden_approval.status_code == 403

    # Reviewer can approve.
    approved = client.post(
        f"/api/v1/runs/{run_id}/approval",
        headers={"X-API-Key": "reviewer-key"},
        json={
            "decision": "approved",
            "reviewer": "Forged Reviewer",
            "note": "Reviewed",
        },
    )

    assert approved.status_code == 200

    # Server identity wins over the user-supplied reviewer.
    assert (
        approved.json()["reviewer"]
        == "Authenticated Reviewer"
    )

    # Operator cannot delete history.
    operator_delete = client.delete(
        "/api/v1/runs/history",
        headers={"X-API-Key": "operator-key"},
    )

    assert operator_delete.status_code == 403

    # Admin can delete history.
    admin_delete = client.delete(
        "/api/v1/runs/history",
        headers={"X-API-Key": "admin-key"},
    )

    assert admin_delete.status_code == 200


def test_queued_run_rejects_request_scoped_gateway_token(monkeypatch):
    settings = Settings(
        api_auth_enabled=True,
        api_operator_keys="operator-key",
        api_admin_keys="admin-key",
    )

    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: settings,
    )

    monkeypatch.setattr(
        api_module.settings,
        "inline_execution",
        False,
    )

    fake_repo = FakeRepo()
    fake_queue = FakeQueue()

    monkeypatch.setattr(
        api_module,
        "repo",
        fake_repo,
    )

    monkeypatch.setattr(
        api_module,
        "queue",
        lambda: fake_queue,
    )

    client = TestClient(api_module.app)

    response = client.post(
        "/api/v1/runs",
        headers={
            "X-API-Key": "operator-key",
            "X-LLM-Gateway-Token": "must-not-enter-redis",
        },
        json={
            "workflow": "content",
            "payload": {},
        },
    )

    assert response.status_code == 400
    assert not fake_queue.calls
    assert not fake_repo.runs
