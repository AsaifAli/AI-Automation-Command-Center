import importlib.util
import sys
from types import ModuleType, SimpleNamespace

# Keep this unit test runnable in lightweight tooling environments. Real
# project environments use the installed psycopg and LangGraph packages.
if "psycopg" not in sys.modules and importlib.util.find_spec("psycopg") is None:
    psycopg_stub = ModuleType("psycopg")
    psycopg_rows_stub = ModuleType("psycopg.rows")
    psycopg_rows_stub.dict_row = object()
    sys.modules.setdefault("psycopg", psycopg_stub)
    sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

if "langgraph" not in sys.modules and importlib.util.find_spec("langgraph") is None:
    langgraph_stub = ModuleType("langgraph")
    langgraph_graph_stub = ModuleType("langgraph.graph")

    class _UnusedStateGraph:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("LangGraph is not used by this isolated worker test")

    langgraph_graph_stub.END = "__end__"
    langgraph_graph_stub.StateGraph = _UnusedStateGraph
    sys.modules.setdefault("langgraph", langgraph_stub)
    sys.modules.setdefault("langgraph.graph", langgraph_graph_stub)

import app.workers.jobs as jobs_module
from app.core.llm_gateway_context import get_llm_gateway_token



def test_workflow_retry_can_decrypt_encrypted_token_without_consuming_repository_ticket(monkeypatch):
    encrypted = b"encrypted-session"
    observed = {}

    class FakeRepo:
        def __init__(self, settings):
            pass

        def mark_running(self, run_id):
            observed["running"] = run_id

        def save_result(self, result):
            observed.setdefault("saved", []).append(result)

    class FakeVault:
        def __init__(self, encryption_key, ttl_seconds):
            observed["ttl"] = ttl_seconds

        def decrypt(self, value):
            assert value == encrypted
            return "short-lived-workflow-bearer"

    class FakeEngine:
        def __init__(self, llm):
            pass

        def run(self, workflow, payload, run_id=None):
            observed["token"] = get_llm_gateway_token()
            return {
                "run_id": run_id,
                "workflow": workflow,
                "status": "completed",
                "output": {"ok": True},
                "evidence": [],
                "duration_ms": 1,
                "created_at": "2026-11-15T00:00:00+00:00",
                "usage": None,
                "warnings": [],
            }

    settings = SimpleNamespace(
        gateway_job_credential_encryption_key="fernet-key",
        gateway_job_credential_ttl_seconds=900,
    )
    monkeypatch.setattr(jobs_module, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs_module, "Repository", FakeRepo)
    monkeypatch.setattr(jobs_module, "GatewayCredentialVault", FakeVault)
    monkeypatch.setattr(jobs_module, "WorkflowEngine", FakeEngine)

    result = jobs_module.execute_run(
        "run-456",
        "competitor",
        {"competitors": ["AI"]},
        encrypted_gateway_token=encrypted,
        gateway_token_ttl_seconds=720,
        persist_success=False,
        persist_failure=False,
    )

    assert result["status"] == "completed"
    assert observed["token"] == "short-lived-workflow-bearer"
    assert observed["ttl"] == 720
    assert "saved" not in observed
    assert get_llm_gateway_token() == ""


def test_workflow_retry_does_not_persist_an_intermediate_failure(monkeypatch):
    observed = {"saved": []}

    class FakeRepo:
        def __init__(self, settings):
            pass

        def mark_running(self, run_id):
            return None

        def save_result(self, result):
            observed["saved"].append(result)

    class FakeEngine:
        def __init__(self, llm):
            pass

        def run(self, workflow, payload, run_id=None):
            raise RuntimeError("temporary provider timeout")

    settings = SimpleNamespace(
        gateway_job_credential_encryption_key="",
        gateway_job_credential_ttl_seconds=900,
    )
    monkeypatch.setattr(jobs_module, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs_module, "Repository", FakeRepo)
    monkeypatch.setattr(jobs_module, "WorkflowEngine", FakeEngine)

    import pytest

    with pytest.raises(RuntimeError, match="temporary provider timeout"):
        jobs_module.execute_run(
            "run-789",
            "competitor",
            {"competitors": ["AI"]},
            persist_failure=False,
        )

    assert observed["saved"] == []
    assert get_llm_gateway_token() == ""


def test_worker_consumes_credential_reference_and_clears_context(monkeypatch):
    encrypted = b"encrypted-session"
    observed = {}

    class FakeRepo:
        def __init__(self, settings):
            pass

        def consume_gateway_job_credential(self, run_id, credential_id):
            observed["consumed"] = (run_id, credential_id)
            return encrypted

        def mark_running(self, run_id):
            observed["running"] = run_id

        def save_result(self, result):
            observed["result"] = result

    class FakeVault:
        def __init__(self, encryption_key, ttl_seconds):
            observed["vault_config"] = (encryption_key, ttl_seconds)

        def decrypt(self, value):
            assert value == encrypted
            return "one-time-bearer-value"

    class FakeEngine:
        def __init__(self, llm):
            pass

        def run(self, workflow, payload, run_id=None):
            observed["active_token_during_run"] = get_llm_gateway_token()
            return {
                "run_id": run_id,
                "workflow": workflow,
                "status": "completed",
                "output": {"ok": True},
                "evidence": [],
                "duration_ms": 1,
                "created_at": "2026-11-15T00:00:00+00:00",
                "usage": None,
                "warnings": [],
            }

    settings = SimpleNamespace(
        gateway_job_credential_encryption_key="fernet-key",
        gateway_job_credential_ttl_seconds=900,
    )
    monkeypatch.setattr(jobs_module, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs_module, "Repository", FakeRepo)
    monkeypatch.setattr(jobs_module, "GatewayCredentialVault", FakeVault)
    monkeypatch.setattr(jobs_module, "WorkflowEngine", FakeEngine)

    result = jobs_module.execute_run(
        "run-123",
        "content",
        {"topics": ["private brief"]},
        gateway_credential_id="credential-ref-123",
    )

    assert observed["consumed"] == ("run-123", "credential-ref-123")
    assert observed["vault_config"] == ("fernet-key", 900)
    assert observed["active_token_during_run"] == "one-time-bearer-value"
    assert result["status"] == "completed"
    assert get_llm_gateway_token() == ""
