
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
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
            "status": "awaiting_approval" if workflow in {"content", "outreach"} else "queued",
            "output": ({"approval": {"status": "pending", "action": "send_outreach" if workflow == "outreach" else "publish_content", "title": "Review requested", "summary": "Test approval request", "items": []}} if workflow in {"content", "outreach"} else {}),
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        }

        return run_id

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def list_runs(self, limit):
        return list(self.runs.values())[:limit]

    def list_pending_approvals(self, limit):
        return [
            {"run_id": run["run_id"], "workflow": run["workflow"], "status": run["status"], "created_at": run["created_at"], "approval": run["output"].get("approval", {}), "output": run["output"]}
            for run in self.runs.values() if run["status"] == "awaiting_approval"
        ][:limit]

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
            "workflow_approvals_pending": sum(run["status"] == "awaiting_approval" for run in self.runs.values()),
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
            "decided_at": datetime.now(UTC),
        }

        run = self.runs[run_id]
        if run["status"] != "awaiting_approval":
            raise ValueError("This run is no longer awaiting approval")
        run["status"] = decision
        run["output"].setdefault("approval", {}).update({"status": decision, "decision": decision, "reviewer": reviewer, "note": note, "execution_status": "not_configured"})
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


def test_queued_gateway_token_is_encrypted_and_only_reference_enters_rq(monkeypatch):
    settings = Settings(
        api_auth_enabled=True,
        api_operator_keys="operator-key",
        api_admin_keys="admin-key",
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module, "verify_flowpilot_gateway_session", lambda token: 600)
    monkeypatch.setattr(api_module.settings, "inline_execution", False)
    monkeypatch.setattr(api_module.settings, "database_url", "postgresql://test/db")
    monkeypatch.setattr(api_module.settings, "llm_gateway_url", "https://gateway.example/v1")
    monkeypatch.setattr(api_module.settings, "gateway_job_credential_encryption_key", "test-fernet-key")
    monkeypatch.setattr(api_module.settings, "gateway_job_credential_ttl_seconds", 900)

    encrypted_values = []

    class FakeVault:
        def __init__(self, encryption_key, ttl_seconds):
            assert encryption_key == "test-fernet-key"
            assert ttl_seconds == 900

        def encrypt(self, token):
            encrypted_values.append(token)
            return b"encrypted-token-bytes"

    monkeypatch.setattr(api_module, "GatewayCredentialVault", FakeVault)

    fake_repo = FakeRepo()
    fake_repo.stored_credentials = []

    def store_credential(run_id, encrypted_token, ttl_seconds):
        fake_repo.stored_credentials.append((run_id, encrypted_token, ttl_seconds))
        return "credential-reference-only"

    fake_repo.store_gateway_job_credential = store_credential
    fake_repo.delete_gateway_job_credential = lambda run_id, credential_id: None
    fake_queue = FakeQueue()
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module, "queue", lambda: fake_queue)

    client = TestClient(api_module.app)
    secret = "must-not-enter-redis"
    response = client.post(
        "/api/v1/runs",
        headers={
            "X-API-Key": "operator-key",
            "X-LLM-Gateway-Token": secret,
        },
        json={"workflow": "content", "payload": {"topics": ["comma, preserved"]}},
    )

    assert response.status_code == 202
    assert encrypted_values == [secret]
    assert fake_repo.stored_credentials[0][1:] == (b"encrypted-token-bytes", 600)
    assert len(fake_queue.calls) == 1
    args, kwargs = fake_queue.calls[0]
    assert args[0].__name__ == "execute_run"
    assert kwargs["gateway_credential_id"] == "credential-reference-only"
    assert kwargs["job_id"] == response.json()["run_id"]
    assert secret not in repr(fake_queue.calls)
    assert secret not in repr(fake_repo.stored_credentials)


def test_queued_gateway_token_fails_closed_without_postgres(monkeypatch):
    settings = Settings(
        api_auth_enabled=True,
        api_operator_keys="operator-key",
        api_admin_keys="admin-key",
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module, "verify_flowpilot_gateway_session", lambda token: 600)
    monkeypatch.setattr(api_module.settings, "inline_execution", False)
    monkeypatch.setattr(api_module.settings, "database_url", "")
    fake_repo = FakeRepo()
    fake_queue = FakeQueue()
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module, "queue", lambda: fake_queue)

    response = TestClient(api_module.app).post(
        "/api/v1/runs",
        headers={"X-API-Key": "operator-key", "X-LLM-Gateway-Token": "short-lived-session"},
        json={"workflow": "content", "payload": {}},
    )

    assert response.status_code == 503
    assert not fake_queue.calls
    assert not fake_repo.runs


def test_queued_gateway_token_fails_closed_without_encryption_key(monkeypatch):
    settings = Settings(
        api_auth_enabled=True,
        api_operator_keys="operator-key",
        api_admin_keys="admin-key",
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module, "verify_flowpilot_gateway_session", lambda token: 600)
    monkeypatch.setattr(api_module.settings, "inline_execution", False)
    monkeypatch.setattr(api_module.settings, "database_url", "postgresql://test/db")
    monkeypatch.setattr(api_module.settings, "llm_gateway_url", "https://gateway.example/v1")
    monkeypatch.setattr(api_module.settings, "gateway_job_credential_encryption_key", "")
    fake_repo = FakeRepo()
    fake_queue = FakeQueue()
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module, "queue", lambda: fake_queue)

    response = TestClient(api_module.app).post(
        "/api/v1/runs",
        headers={"X-API-Key": "operator-key", "X-LLM-Gateway-Token": "short-lived-session"},
        json={"workflow": "content", "payload": {}},
    )

    assert response.status_code == 503
    assert not fake_queue.calls
    assert not fake_repo.runs


def test_gateway_session_verifier_requires_active_flowpilot_audience(monkeypatch):
    import time

    from fastapi import HTTPException

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        response = None

        def __init__(self, **kwargs):
            self.options = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, url, headers):
            assert url == "https://gateway.example/v1/demo/session/status"
            assert headers == {"Authorization": "Bearer test-session-token"}
            assert self.options["follow_redirects"] is False
            assert self.options["trust_env"] is False
            return self.response

    monkeypatch.setattr(api_module.settings, "llm_gateway_url", "https://gateway.example/v1")
    valid = FakeResponse({
        "active": True,
        "project": "flowpilot",
        "session_project": "portfolio",
        "expires_at": int(time.time()) + 300,
    })
    FakeClient.response = valid
    monkeypatch.setattr(api_module.httpx, "Client", FakeClient)

    remaining = api_module.verify_flowpilot_gateway_session("test-session-token")
    assert 0 < remaining <= 300

    FakeClient.response = FakeResponse({
        "active": True,
        "project": "quotesense",
        "session_project": "portfolio",
        "expires_at": int(time.time()) + 300,
    })
    with pytest.raises(HTTPException) as exc_info:
        api_module.verify_flowpilot_gateway_session("test-session-token")
    assert exc_info.value.status_code == 403

    FakeClient.response = FakeResponse({
        "active": False,
        "project": "flowpilot",
        "session_project": "portfolio",
        "expires_at": int(time.time()) + 300,
    })
    with pytest.raises(HTTPException) as exc_info:
        api_module.verify_flowpilot_gateway_session("test-session-token")
    assert exc_info.value.status_code == 403


def test_inline_gateway_token_is_request_scoped_and_not_persisted(monkeypatch):
    from app.core.llm_gateway_context import get_llm_gateway_token

    settings = Settings(
        api_auth_enabled=True,
        api_operator_keys="operator-key",
        api_admin_keys="admin-key",
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module, "verify_flowpilot_gateway_session", lambda token: 600)
    monkeypatch.setattr(api_module.settings, "inline_execution", True)
    fake_repo = FakeRepo()
    monkeypatch.setattr(api_module, "repo", fake_repo)
    observed = {}

    def fake_execute_run(run_id, workflow, payload, *, use_request_context=False):
        observed["token_during_execution"] = get_llm_gateway_token()
        observed["use_request_context"] = use_request_context
        return {"run_id": run_id, "workflow": workflow, "status": "completed"}

    monkeypatch.setattr(api_module, "execute_run", fake_execute_run)
    token = "valid-flowpilot-gateway-bearer"
    response = TestClient(api_module.app).post(
        "/api/v1/runs",
        headers={
            "X-API-Key": "operator-key",
            "X-LLM-Gateway-Token": token,
        },
        json={"workflow": "content", "payload": {"topics": ["Finance operations"]}},
    )

    assert response.status_code == 202
    assert observed["token_during_execution"] == token
    assert observed["use_request_context"] is True
    assert token not in repr(fake_repo.runs)
