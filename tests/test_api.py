from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api.main as api_module


class FakeRepo:
    def __init__(self):
        self.run = None

    def init_db(self):
        return None

    def create_pending_run(self, workflow, owner_id="legacy"):
        requires_approval = workflow in {"content", "outreach"}
        self.run = {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "owner_id": owner_id,
            "workflow": workflow,
            "status": "awaiting_approval" if requires_approval else "queued",
            "output": ({"approval": {"status": "pending", "action": "send_outreach" if workflow == "outreach" else "publish_content", "title": "Review requested", "summary": "Test approval request", "items": []}} if requires_approval else {}),
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        }
        return self.run["run_id"]

    def ensure_pending_run(self, run_id, workflow, owner_id):
        if self.run and self.run["run_id"] == run_id:
            if self.run["workflow"] != workflow or self.run["owner_id"] != owner_id:
                raise ValueError("Workflow run scope does not match the existing run.")
            return
        self.run = {
            "run_id": run_id,
            "owner_id": owner_id,
            "workflow": workflow,
            "status": "queued",
            "output": {},
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        }

    def save_result(self, result):
        if not self.run or self.run["run_id"] != result["run_id"]:
            raise KeyError("Run not found")
        self.run.update({
            "status": result["status"],
            "output": result.get("output", {}),
            "evidence": result.get("evidence", []),
            "duration_ms": result.get("duration_ms", 0),
            "usage": result.get("usage"),
            "warnings": result.get("warnings", []),
        })

    def get_run(self, run_id, *, owner_id=None, include_all=False):
        if not self.run or self.run["run_id"] != run_id:
            return None
        if (
            not include_all
            and owner_id is not None
            and self.run.get("owner_id", "legacy") != owner_id
        ):
            return None
        return self.run

    def list_runs(self, limit, *, owner_id=None, include_all=False):
        if not self.run:
            return []
        if (
            not include_all
            and owner_id is not None
            and self.run.get("owner_id", "legacy") != owner_id
        ):
            return []
        return [self.run][:limit]

    def clear_history(self):
        self.run = None
        return 1

    def metrics(self):
        return {"workflow_runs_total": 0, "workflow_runs_completed": 0, "workflow_runs_failed": 0, "workflow_runs_queued": 1 if self.run else 0, "workflow_runs_running": 0, "workflow_approvals_total": 0, "workflow_approvals_approved": 0}

    def create_approval(self, run_id, decision, reviewer, note):
        if not self.run or self.run["status"] != "awaiting_approval":
            raise ValueError("This run is no longer awaiting approval")
        self.run["status"] = decision
        self.run["output"].setdefault("approval", {}).update({"status": decision, "decision": decision, "reviewer": reviewer, "note": note, "execution_status": "not_configured"})
        return {"approval_id": "22222222-2222-2222-2222-222222222222", "run_id": run_id, "decision": decision, "reviewer": reviewer, "note": note, "decided_at": datetime.now(UTC)}


class FakeQueue:
    last_enqueue = None

    def enqueue(self, *args, **kwargs):
        type(self).last_enqueue = {"args": args, "kwargs": dict(kwargs)}
        return SimpleNamespace(id=kwargs.get("job_id"))


api_module.repo = FakeRepo()
api_module.queue = lambda: FakeQueue()

client = TestClient(api_module.app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_queue_workflow():
    response = client.post("/api/v1/runs", json={"workflow": "competitor", "payload": {"competitors": ["AI"]}})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert FakeQueue.last_enqueue["kwargs"]["job_timeout"] == api_module.settings.workflow_job_timeout_seconds




def test_upstash_trigger_encodes_route_and_limits_parallel_callbacks(monkeypatch):
    import asyncio

    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"workflowRunId": "wfr_flowpilot-test"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setattr(api_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_module.settings, "qstash_url", "https://qstash-eu-central-1.upstash.io")
    monkeypatch.setattr(api_module.settings, "qstash_token", "private-qstash-token")
    monkeypatch.setattr(api_module.settings, "upstash_workflow_url", "https://flowpilot.example.com")

    result = asyncio.run(api_module._trigger_upstash_workflow({"run_id": "id"}, "id"))

    assert result == "wfr_flowpilot-test"
    assert captured["url"] == (
        "https://qstash-eu-central-1.upstash.io/v2/trigger/"
        "https://flowpilot.example.com/internal/qstash/workflow"
    )
    assert captured["headers"]["Authorization"] == "Bearer private-qstash-token"
    assert captured["headers"]["Upstash-Flow-Control-Key"] == "flowpilot-global"
    assert captured["headers"]["Upstash-Flow-Control-Value"] == "parallelism=2, rate=2, period=1s"
    assert captured["body"] == {"run_id": "id"}
    assert captured["client_options"]["trust_env"] is False


def test_upstash_trigger_reports_sanitized_qstash_rejection(monkeypatch):
    import asyncio

    class FakeResponse:
        status_code = 400

        @staticmethod
        def json():
            return {
                "error": "invalid flow-control header; Authorization: Bearer sensitive-qstash-value"
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setattr(api_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_module.settings, "qstash_url", "https://qstash-eu-central-1.upstash.io")
    monkeypatch.setattr(api_module.settings, "qstash_token", "private-qstash-token")
    monkeypatch.setattr(api_module.settings, "upstash_workflow_url", "https://flowpilot.example.com")

    try:
        asyncio.run(api_module._trigger_upstash_workflow({"run_id": "id"}, "id"))
    except RuntimeError as exc:
        assert "HTTP 400" in str(exc)
        assert "invalid flow-control header" in str(exc)
        assert "sensitive-qstash-value" not in str(exc)
        assert "private-qstash-token" not in str(exc)
    else:
        raise AssertionError("Expected QStash rejection to raise RuntimeError")


def test_upstash_workflow_dispatch_encrypts_byok_before_queueing(monkeypatch):
    captured = {}

    async def fake_trigger(envelope, run_id):
        captured["envelope"] = envelope
        captured["run_id"] = run_id
        return "wfr_flowpilot-test"

    class FakeVault:
        def __init__(self, key, ttl_seconds):
            captured["vault_ttl"] = ttl_seconds

        def encrypt(self, token):
            captured["raw_token_seen_by_vault"] = token
            return b"encrypted-ciphertext"

    monkeypatch.setattr(api_module, "_trigger_upstash_workflow", fake_trigger)
    monkeypatch.setattr(api_module, "GatewayCredentialVault", FakeVault)
    monkeypatch.setattr(
        api_module,
        "verify_flowpilot_gateway_session",
        lambda token: api_module.GatewaySessionLifetime(720, "a" * 64),
    )
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    monkeypatch.setattr(api_module.settings, "gateway_job_credential_encryption_key", "test-fernet-key")
    monkeypatch.setattr(api_module, "repo", FakeRepo())

    raw_token = "raw-bearer-secret"
    response = client.post(
        "/api/v1/runs",
        headers={"X-LLM-Gateway-Token": raw_token},
        json={"workflow": "competitor", "payload": {"competitors": ["AI"]}},
    )

    assert response.status_code == 202
    assert captured["raw_token_seen_by_vault"] == raw_token
    assert captured["vault_ttl"] == 720
    assert captured["envelope"]["gateway_token_ciphertext"] == "ZW5jcnlwdGVkLWNpcGhlcnRleHQ="
    assert raw_token not in repr(captured["envelope"])
    assert response.json()["status"] == "queued"


def test_upstash_workflow_dispatch_failure_marks_run_failed(monkeypatch):
    async def fail_trigger(envelope, run_id):
        raise RuntimeError("private provider or QStash response must not be surfaced")

    monkeypatch.setattr(api_module, "_trigger_upstash_workflow", fail_trigger)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    fake_repo = FakeRepo()
    monkeypatch.setattr(api_module, "repo", fake_repo)

    response = client.post(
        "/api/v1/runs",
        json={"workflow": "competitor", "payload": {"competitors": ["AI"]}},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Workflow could not be queued. Retry after checking the background execution connection."
    )
    assert fake_repo.run["status"] == "failed"


def test_upstash_workflow_envelope_rejects_malformed_owner_and_ciphertext():
    import uuid

    import pytest

    valid = {
        "run_id": str(uuid.uuid4()),
        "workflow": "competitor",
        "payload": {},
        "owner_id": "owner-1",
    }
    assert api_module._validate_upstash_workflow_payload(valid)["encrypted_gateway_token"] is None

    with pytest.raises(ValueError, match="owner scope"):
        api_module._validate_upstash_workflow_payload({**valid, "owner_id": ""})
    with pytest.raises(ValueError, match="Encrypted Gateway credential"):
        api_module._validate_upstash_workflow_payload({**valid, "gateway_token_ciphertext": "not-base64"})


def test_clear_run_history():
    client.post("/api/v1/runs", json={"workflow": "kpi", "payload": {"updates": []}})
    response = client.delete("/api/v1/runs/history")
    assert response.status_code == 200
    assert response.json()["deleted_runs"] == 1


def test_outreach_approval():
    response = client.post("/api/v1/runs", json={"workflow": "outreach", "payload": {"candidates": [{"name": "A"}]}})
    run_id = response.json()["run_id"]
    approval = client.post(f"/api/v1/runs/{run_id}/approval", json={"decision": "approved", "reviewer": "Tester", "note": "OK"})
    assert approval.status_code == 200
    assert approval.json()["decision"] == "approved"


def test_json_serialization_of_datetime_evidence():
    from datetime import datetime

    from app.storage.repository import _json_dumps

    payload = {"observed_at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC)}
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


def test_upstash_initial_payload_parser_decodes_nested_json_envelopes():
    import json

    envelope = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "workflow": "competitor",
        "payload": {"competitors": ["AI"]},
        "owner_id": "owner-1",
    }

    assert api_module._parse_upstash_workflow_initial_payload(envelope) == envelope
    assert api_module._parse_upstash_workflow_initial_payload(json.dumps(envelope)) == envelope
    assert api_module._parse_upstash_workflow_initial_payload(
        json.dumps(json.dumps(envelope))
    ) == envelope


def test_run_controls_pause_resume_and_stop_only_the_owned_run(monkeypatch):
    fake_repo = FakeRepo()
    fake_repo.run = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "owner_id": "local-development",
        "workflow": "competitor",
        "status": "queued",
        "output": {},
        "evidence": [],
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }

    def pause_run(run_id):
        assert fake_repo.run["run_id"] == run_id
        fake_repo.run["resume_status"] = fake_repo.run["status"]
        fake_repo.run["status"] = "paused"
        return dict(fake_repo.run)

    def resume_run(run_id):
        assert fake_repo.run["run_id"] == run_id
        fake_repo.run["status"] = fake_repo.run.pop("resume_status", "queued")
        return dict(fake_repo.run)

    def stop_run(run_id):
        assert fake_repo.run["run_id"] == run_id
        fake_repo.run["status"] = "cancelled"
        return dict(fake_repo.run)

    fake_repo.pause_run = pause_run
    fake_repo.resume_run = resume_run
    fake_repo.stop_run = stop_run
    cancelled = []

    async def fake_cancel(run_id):
        cancelled.append(run_id)

    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    monkeypatch.setattr(api_module, "_cancel_upstash_workflow", fake_cancel)

    run_id = fake_repo.run["run_id"]
    paused = client.post(f"/api/v1/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    stopped = client.post(f"/api/v1/runs/{run_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    assert cancelled == [run_id]


def test_run_controls_do_not_disclose_another_owners_run(monkeypatch):
    fake_repo = FakeRepo()
    fake_repo.run = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "owner_id": "someone-else",
        "workflow": "competitor",
        "status": "queued",
        "output": {},
        "evidence": [],
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    monkeypatch.setattr(api_module.settings, "api_auth_enabled", True)
    monkeypatch.setattr(api_module.settings, "api_operator_keys", "operator-secret")
    monkeypatch.setattr(api_module.settings, "api_admin_keys", "")
    monkeypatch.setattr(api_module.settings, "api_key", "")

    response = client.post(
        f"/api/v1/runs/{fake_repo.run['run_id']}/pause",
        headers={"X-API-Key": "operator-secret"},
    )

    assert response.status_code == 404
    assert fake_repo.run["status"] == "queued"


def test_upstash_trigger_disables_duplicate_workflow_level_retries(monkeypatch):
    import asyncio

    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"workflowRunId": "wfr_flowpilot-test"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, headers, json):
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(api_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_module.settings, "qstash_url", "https://qstash-eu-central-1.upstash.io")
    monkeypatch.setattr(api_module.settings, "qstash_token", "private-qstash-token")
    monkeypatch.setattr(api_module.settings, "upstash_workflow_url", "https://flowpilot.example.com")

    asyncio.run(api_module._trigger_upstash_workflow({"run_id": "id"}, "id"))

    assert captured["headers"]["Upstash-Workflow-Sdk-Version"] == "1"
    assert captured["headers"]["Upstash-Retries"] == "0"
    assert captured["headers"]["Upstash-Flow-Control-Key"] == "flowpilot-global"


def test_qstash_stop_cancels_only_the_requested_workflow_run(monkeypatch):
    import asyncio

    captured = {}

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def delete(self, url, *, headers, params):
            captured.update({"url": url, "headers": headers, "params": params})
            return FakeResponse()

    monkeypatch.setattr(api_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_module.settings, "qstash_url", "https://qstash-eu-central-1.upstash.io")
    monkeypatch.setattr(api_module.settings, "qstash_token", "private-qstash-token")

    asyncio.run(api_module._cancel_upstash_workflow(
        "11111111-1111-1111-1111-111111111111"
    ))

    assert captured["url"] == (
        "https://qstash-eu-central-1.upstash.io/v2/workflows/runs/"
        "wfr_flowpilot-11111111-1111-1111-1111-111111111111"
    )
    assert captured["params"] == {"cancel": "true"}
    assert captured["headers"]["Authorization"] == "Bearer private-qstash-token"
    assert captured["client_options"]["trust_env"] is False
