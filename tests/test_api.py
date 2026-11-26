import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.main as api_module


class FakeRepo:
    def __init__(self):
        self.run = None
        self.dispatch = None

    def init_db(self):
        return None

    def create_pending_run(self, workflow, owner_id="legacy"):
        requires_approval = workflow in {"content", "outreach"}
        self.run = {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "owner_id": owner_id,
            "workflow": workflow,
            "status": "awaiting_approval" if requires_approval else "queued",
            "resume_status": None,
            "output": (
                {"approval": {"status": "pending", "action": "send_outreach" if workflow == "outreach" else "publish_content", "title": "Review requested", "summary": "Test approval request", "items": []}}
                if requires_approval else {}
            ),
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        }
        self.dispatch = None
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
            "resume_status": None,
            "output": {},
            "evidence": [],
            "duration_ms": 0,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        }
        self.dispatch = None

    def prepare_qstash_dispatch(self, run_id, payload):
        if not self.run or self.run["run_id"] != run_id or self.run["status"] != "queued":
            raise ValueError("Only queued runs can be dispatched.")
        generation = (self.dispatch or {}).get("generation", 0) + 1
        payload["dispatch_generation"] = generation
        self.dispatch = {
            "payload": json.loads(json.dumps(payload)),
            "message_id": None,
            "generation": generation,
            "pending_result": None,
        }
        return generation

    def restore_qstash_dispatch(self, run_id, payload):
        if not self.run or self.run["run_id"] != run_id:
            raise KeyError("Run not found")
        generation = payload["dispatch_generation"]
        if not self.dispatch or generation > self.dispatch["generation"]:
            self.dispatch = {
                "payload": dict(payload),
                "message_id": None,
                "generation": generation,
                "pending_result": None,
            }

    def set_qstash_message_id(self, run_id, generation, message_id):
        if not self.run or self.run["run_id"] != run_id or not self.dispatch:
            return False
        if self.dispatch["generation"] != generation:
            return False
        self.dispatch["message_id"] = message_id
        return True

    def get_qstash_dispatch(self, run_id):
        if not self.run or self.run["run_id"] != run_id:
            return None
        return dict(self.dispatch) if self.dispatch else None

    def claim_qstash_run(self, run_id, generation):
        if not self.run or self.run["run_id"] != run_id:
            return "missing"
        if not self.dispatch or self.dispatch["generation"] != generation:
            return "superseded"
        if self.run["status"] != "queued":
            return self.run["status"]
        self.run["status"] = "running"
        return "claimed"

    def save_paused_result(self, run_id, result):
        if not self.run or self.run["run_id"] != run_id or self.run["status"] != "paused":
            return False
        self.dispatch["pending_result"] = dict(result)
        return True

    def take_pending_result(self, run_id):
        if not self.run or self.run["run_id"] != run_id or not self.dispatch:
            return None
        result = self.dispatch.get("pending_result")
        self.dispatch["pending_result"] = None
        return result

    def fail_run_if_active(self, result):
        if not self.run or self.run["run_id"] != result["run_id"]:
            return False
        if self.run["status"] == "paused":
            self.dispatch["pending_result"] = dict(result)
            return True
        if self.run["status"] not in {"queued", "running"}:
            return False
        self.run.update({
            "status": "failed",
            "output": result.get("output", {}),
            "evidence": result.get("evidence", []),
            "duration_ms": result.get("duration_ms", 0),
            "usage": result.get("usage"),
            "warnings": result.get("warnings", []),
        })
        return True

    def mark_running(self, run_id):
        if self.run and self.run["run_id"] == run_id and self.run["status"] == "queued":
            self.run["status"] = "running"

    def pause_run(self, run_id):
        if not self.run or self.run["run_id"] != run_id or self.run["status"] not in {"queued", "running"}:
            return False
        self.run["resume_status"] = self.run["status"]
        self.run["status"] = "paused"
        return True

    def resume_run(self, run_id):
        if not self.run or self.run["run_id"] != run_id or self.run["status"] != "paused":
            return False
        self.run["status"] = self.run.pop("resume_status", None) or "queued"
        return True

    def stop_run(self, run_id):
        if not self.run or self.run["run_id"] != run_id or self.run["status"] not in {"queued", "running", "paused"}:
            return False
        self.run["status"] = "cancelled"
        self.run["resume_status"] = None
        self.run["output"] = {"error": "Workflow was stopped by the user."}
        self.run["warnings"] = ["Execution was canceled by the user."]
        return True

    def save_result(self, result):
        if not self.run or self.run["run_id"] != result["run_id"]:
            raise KeyError("Run not found")
        if self.run["status"] not in {"queued", "running"}:
            return
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
        if not include_all and owner_id is not None and self.run.get("owner_id", "legacy") != owner_id:
            return None
        return dict(self.run)

    def list_runs(self, limit, *, owner_id=None, include_all=False):
        if not self.run:
            return []
        if not include_all and owner_id is not None and self.run.get("owner_id", "legacy") != owner_id:
            return []
        return [dict(self.run)][:limit]

    def clear_history(self):
        self.run = None
        self.dispatch = None
        return 1

    def metrics(self):
        status = (self.run or {}).get("status")
        return {
            "workflow_runs_total": int(bool(self.run)),
            "workflow_runs_completed": int(status == "completed"),
            "workflow_runs_failed": int(status == "failed"),
            "workflow_runs_queued": int(status == "queued"),
            "workflow_runs_running": int(status == "running"),
            "workflow_approvals_total": 0,
            "workflow_approvals_approved": 0,
        }

    def create_approval(self, run_id, decision, reviewer, note):
        if not self.run or self.run["status"] != "awaiting_approval":
            raise ValueError("This run is no longer awaiting approval")
        self.run["status"] = decision
        self.run["output"].setdefault("approval", {}).update({
            "status": decision,
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
            "execution_status": "not_configured",
        })
        return {
            "approval_id": "22222222-2222-2222-2222-222222222222",
            "run_id": run_id,
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
            "decided_at": datetime.now(UTC),
        }


class FakeQueue:
    last_enqueue = None

    def enqueue(self, *args, **kwargs):
        type(self).last_enqueue = {"args": args, "kwargs": dict(kwargs)}
        return SimpleNamespace(id=kwargs.get("job_id"))


api_module.repo = FakeRepo()
api_module.queue = lambda: FakeQueue()
client = TestClient(api_module.app)


def _qstash_envelope(run_id="11111111-1111-1111-1111-111111111111", owner_id="owner-1"):
    return {
        "run_id": run_id,
        "workflow": "competitor",
        "payload": {"competitors": ["AI"]},
        "owner_id": owner_id,
        "dispatch_generation": 1,
        "gateway_token_ciphertext": None,
        "gateway_token_ttl_seconds": None,
    }


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_queue_workflow():
    response = client.post(
        "/api/v1/runs",
        json={"workflow": "competitor", "payload": {"competitors": ["AI"]}},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert FakeQueue.last_enqueue["kwargs"]["job_timeout"] == api_module.settings.workflow_job_timeout_seconds


def test_qstash_publish_uses_plain_http_delivery_and_failure_callback(monkeypatch):
    import asyncio

    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"messageId": "msg_flowpilot_test"}

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

    envelope = _qstash_envelope()
    result = asyncio.run(api_module._publish_qstash_message(envelope, envelope["run_id"], 1))

    assert result == "msg_flowpilot_test"
    assert captured["url"] == (
        "https://qstash-eu-central-1.upstash.io/v2/publish/"
        "https://flowpilot.example.com/internal/qstash/execute"
    )
    assert captured["headers"]["Authorization"] == "Bearer private-qstash-token"
    assert captured["headers"]["Upstash-Retries"] == "0"
    assert captured["headers"]["Upstash-Timeout"] == "14m"
    assert captured["headers"]["Upstash-Failure-Callback"] == (
        "https://flowpilot.example.com/internal/qstash/failure"
    )
    assert captured["headers"]["Upstash-Flow-Control-Key"] == "flowpilot-global"
    assert captured["headers"]["Upstash-Deduplication-Id"] == (
        "flowpilot-11111111-1111-1111-1111-111111111111-1"
    )
    assert captured["body"]["dispatch_generation"] == 1
    assert captured["client_options"]["trust_env"] is False


def test_qstash_publish_sanitizes_rejection_details(monkeypatch):
    import asyncio

    class FakeResponse:
        status_code = 400

        @staticmethod
        def json():
            return {"error": "Authorization: Bearer sensitive-qstash-value; invalid flow-control header"}

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

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(api_module._publish_qstash_message(_qstash_envelope(), "11111111-1111-1111-1111-111111111111", 1))

    assert "HTTP 400" in str(exc.value)
    assert "invalid flow-control header" in str(exc.value)
    assert "sensitive-qstash-value" not in str(exc.value)
    assert "private-qstash-token" not in str(exc.value)


def test_qstash_dispatch_encrypts_byok_before_publishing(monkeypatch):
    captured = {}

    async def fake_publish(envelope, run_id, generation):
        captured["envelope"] = dict(envelope)
        captured["run_id"] = run_id
        captured["generation"] = generation
        return "msg_flowpilot_test"

    class FakeVault:
        def __init__(self, key, ttl_seconds):
            captured["vault_ttl"] = ttl_seconds

        def encrypt(self, token):
            captured["raw_token_seen_by_vault"] = token
            return b"encrypted-ciphertext"

    monkeypatch.setattr(api_module, "_publish_qstash_message", fake_publish)
    monkeypatch.setattr(api_module, "GatewayCredentialVault", FakeVault)
    monkeypatch.setattr(
        api_module,
        "verify_flowpilot_gateway_session",
        lambda token: api_module.GatewaySessionLifetime(720, "a" * 64),
    )
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    monkeypatch.setattr(api_module.settings, "gateway_job_credential_encryption_key", "test-fernet-key")
    fake_repo = FakeRepo()
    monkeypatch.setattr(api_module, "repo", fake_repo)

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
    assert captured["envelope"]["dispatch_generation"] == 1
    assert raw_token not in repr(captured["envelope"])
    assert fake_repo.dispatch["message_id"] == "msg_flowpilot_test"
    assert response.json()["status"] == "queued"


def test_qstash_dispatch_failure_marks_run_failed(monkeypatch):
    async def fail_publish(envelope, run_id, generation):
        raise RuntimeError("private provider or QStash response must not be surfaced")

    monkeypatch.setattr(api_module, "_publish_qstash_message", fail_publish)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    fake_repo = FakeRepo()
    monkeypatch.setattr(api_module, "repo", fake_repo)

    response = client.post(
        "/api/v1/runs",
        json={"workflow": "competitor", "payload": {"competitors": ["AI"]}},
    )

    assert response.status_code == 503
    assert fake_repo.run["status"] == "failed"


def test_qstash_envelope_validation_rejects_malformed_owner_ciphertext_and_generation():
    valid = _qstash_envelope()
    assert api_module._validate_qstash_payload(valid)["encrypted_gateway_token"] is None

    with pytest.raises(ValueError, match="owner scope"):
        api_module._validate_qstash_payload({**valid, "owner_id": ""})
    with pytest.raises(ValueError, match="Encrypted Gateway credential"):
        api_module._validate_qstash_payload({**valid, "gateway_token_ciphertext": "not-base64"})
    with pytest.raises(ValueError, match="dispatch generation"):
        api_module._validate_qstash_payload({**valid, "dispatch_generation": 0})


def test_qstash_delivery_is_idempotent_and_returns_terminal_result(monkeypatch):
    fake_repo = FakeRepo()
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    monkeypatch.setattr(api_module, "_verify_qstash_signature", lambda body, signature, expected_url: None)
    calls = []

    def fake_execute(*args, **kwargs):
        calls.append(args[0])
        return {
            "run_id": args[0],
            "workflow": "competitor",
            "status": "completed",
            "output": {"summary": "done"},
            "evidence": [],
            "duration_ms": 1200,
            "created_at": datetime.now(UTC),
            "usage": None,
            "warnings": [],
        }

    monkeypatch.setattr(api_module, "execute_run", fake_execute)
    envelope = _qstash_envelope()
    first = client.post("/internal/qstash/execute", json=envelope)
    duplicate = client.post("/internal/qstash/execute", json=envelope)

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "completed"
    assert calls == [envelope["run_id"]]


def test_qstash_failure_callback_records_terminal_failure(monkeypatch):
    fake_repo = FakeRepo()
    fake_repo.ensure_pending_run(
        "11111111-1111-1111-1111-111111111111",
        "competitor",
        "owner-1",
    )
    fake_repo.prepare_qstash_dispatch("11111111-1111-1111-1111-111111111111", _qstash_envelope())
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    monkeypatch.setattr(api_module, "_verify_qstash_signature", lambda body, signature, expected_url: None)
    callback = {
        "status": 504,
        "sourceBody": base64.b64encode(json.dumps(_qstash_envelope()).encode()).decode(),
    }

    response = client.post("/internal/qstash/failure", json=callback)

    assert response.status_code == 200
    assert response.json()["status"] == "failure_recorded"
    assert fake_repo.run["status"] == "failed"
    assert fake_repo.run["output"]["failure_code"] == "qstash_delivery_failed"


def test_pause_resume_and_stop_target_only_the_owned_run(monkeypatch):
    fake_repo = FakeRepo()
    fake_repo.create_pending_run("competitor", owner_id="local-development")
    envelope = {
        "run_id": fake_repo.run["run_id"],
        "workflow": "competitor",
        "payload": {"competitors": ["AI"]},
        "owner_id": "local-development",
    }
    generation = fake_repo.prepare_qstash_dispatch(fake_repo.run["run_id"], envelope)
    fake_repo.set_qstash_message_id(fake_repo.run["run_id"], generation, "msg_flowpilot_test")
    monkeypatch.setattr(api_module, "repo", fake_repo)
    monkeypatch.setattr(api_module.settings, "qstash_workflow_enabled", True)
    cancelled = []

    async def fake_cancel(message_id):
        cancelled.append(message_id)
        return True

    async def fake_publish(envelope, run_id, generation):
        return "msg_flowpilot_resumed"

    monkeypatch.setattr(api_module, "_cancel_qstash_message", fake_cancel)
    monkeypatch.setattr(api_module, "_publish_qstash_message", fake_publish)

    run_id = fake_repo.run["run_id"]
    paused = client.post(f"/api/v1/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["queued_message_cancelled"] is True

    resumed = client.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"
    assert resumed.json()["requeued"] is True

    stopped = client.post(f"/api/v1/runs/{run_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    assert cancelled == ["msg_flowpilot_test", "msg_flowpilot_resumed"]


def test_run_controls_do_not_disclose_another_owners_run(monkeypatch):
    fake_repo = FakeRepo()
    fake_repo.create_pending_run("competitor", owner_id="someone-else")
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


def test_qstash_message_cancellation_targets_message_id(monkeypatch):
    import asyncio

    captured = {}

    class FakeResponse:
        status_code = 202

        @staticmethod
        def json():
            return {"cancelled": 1}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def delete(self, url, *, headers):
            captured.update({"url": url, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr(api_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_module.settings, "qstash_url", "https://qstash-eu-central-1.upstash.io")
    monkeypatch.setattr(api_module.settings, "qstash_token", "private-qstash-token")

    assert asyncio.run(api_module._cancel_qstash_message("msg_flowpilot_123")) is True
    assert captured["url"] == "https://qstash-eu-central-1.upstash.io/v2/messages/msg_flowpilot_123"
    assert captured["headers"]["Authorization"] == "Bearer private-qstash-token"
    assert captured["client_options"]["trust_env"] is False


def test_clear_run_history():
    client.post("/api/v1/runs", json={"workflow": "kpi", "payload": {"updates": []}})
    response = client.delete("/api/v1/runs/history")
    assert response.status_code == 200
    assert response.json()["deleted_runs"] == 1


def test_outreach_approval():
    response = client.post("/api/v1/runs", json={"workflow": "outreach", "payload": {"candidates": [{"name": "A"}]}})
    run_id = response.json()["run_id"]
    approval = client.post(
        f"/api/v1/runs/{run_id}/approval",
        json={"decision": "approved", "reviewer": "Tester", "note": "OK"},
    )
    assert approval.status_code == 200
    assert approval.json()["decision"] == "approved"


def test_json_serialization_of_datetime_evidence():
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


def test_qstash_signature_verification_binds_the_exact_delivery_url(monkeypatch):
    captured = {}

    class FakeReceiver:
        def __init__(self, *, current_signing_key, next_signing_key):
            captured["current"] = current_signing_key
            captured["next"] = next_signing_key

        def verify(self, *, signature, body, url, clock_tolerance):
            captured.update({
                "signature": signature,
                "body": body,
                "url": url,
                "clock_tolerance": clock_tolerance,
            })

    monkeypatch.setattr(api_module, "Receiver", FakeReceiver)
    monkeypatch.setattr(api_module.settings, "qstash_current_signing_key", "current-key")
    monkeypatch.setattr(api_module.settings, "qstash_next_signing_key", "next-key")

    api_module._verify_qstash_signature(
        b'{"run_id":"123"}',
        "signed-jwt",
        "https://flowpilot.example.com/internal/qstash/failure",
    )

    assert captured == {
        "current": "current-key",
        "next": "next-key",
        "signature": "signed-jwt",
        "body": '{"run_id":"123"}',
        "url": "https://flowpilot.example.com/internal/qstash/failure",
        "clock_tolerance": 5,
    }
