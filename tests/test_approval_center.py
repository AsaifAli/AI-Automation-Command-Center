from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.storage.repository import Repository
from app.workflows.orchestrator import WorkflowEngine


class StaticAgent:
    def __init__(self, result):
        self.result = result

    def run(self, _payload):
        return dict(self.result)


def _engine(workflow, result):
    engine = WorkflowEngine.__new__(WorkflowEngine)
    engine.agents = {workflow: StaticAgent(result)}
    engine.llm = SimpleNamespace(
        settings=SimpleNamespace(
            demo_mode=True,
            llm_provider="demo",
            openai_model="deterministic-demo",
        ),
        gateway_session_active=False,
    )
    return engine


def test_content_and_outreach_workflows_stop_at_approval_checkpoint():
    cases = [
        (
            "content",
            {"posts": [{"topic": "AI operations", "channel": "linkedin", "draft": "Draft"}], "_usage": {}},
            "publish_content",
        ),
        (
            "outreach",
            {"candidates": [{"name": "Partner", "channel": "email", "message": "Draft", "approval_required": True}], "_usage": {}},
            "send_outreach",
        ),
    ]
    for workflow, agent_result, action in cases:
        result = _engine(workflow, agent_result).run(workflow, {}, run_id=f"test-{workflow}")
        assert result["status"] == "awaiting_approval"
        assert result["output"]["approval"]["status"] == "pending"
        assert result["output"]["approval"]["action"] == action


def test_analysis_only_workflow_does_not_create_an_approval_gate():
    result = _engine("kpi", {"normalized_updates": [], "_usage": {}}).run("kpi", {}, run_id="test-kpi")
    assert result["status"] == "completed"
    assert "approval" not in result["output"]


def _create_pending_run(repo: Repository, workflow: str = "content") -> str:
    run_id = repo.create_pending_run(workflow)
    repo.mark_running(run_id)
    now = datetime.now(UTC)
    repo.save_result(
        {
            "run_id": run_id,
            "workflow": workflow,
            "status": "awaiting_approval",
            "output": {
                "posts": [{"topic": "AI operations", "channel": "linkedin", "draft": "Draft"}],
                "approval": {
                    "status": "pending",
                    "action": "publish_content",
                    "title": "Review content",
                    "summary": "A draft is ready for review.",
                    "items": [{"topic": "AI operations", "channel": "linkedin", "draft": "Draft"}],
                    "created_at": now.isoformat(),
                },
            },
            "evidence": [],
            "duration_ms": 12,
            "created_at": now,
            "usage": None,
            "warnings": [],
        }
    )
    return run_id


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("approved", "approved"), ("rejected", "rejected")],
)
def test_approval_decision_is_persisted_once_and_is_not_external_execution(decision, expected_status):
    repo = Repository(SimpleNamespace(database_url=""))
    run_id = _create_pending_run(repo)

    pending = repo.list_pending_approvals()
    assert any(item["run_id"] == run_id for item in pending)

    recorded = repo.create_approval(run_id, decision, "Authenticated Reviewer", "Reviewed the draft")
    assert recorded["decision"] == decision

    run = repo.get_run(run_id)
    assert run["status"] == expected_status
    assert run["output"]["approval"]["status"] == expected_status
    assert run["output"]["approval"]["reviewer"] == "Authenticated Reviewer"
    assert run["output"]["approval"]["execution_status"] == "not_configured"
    assert not any(item["run_id"] == run_id for item in repo.list_pending_approvals())

    with pytest.raises(ValueError, match="no longer awaiting approval"):
        repo.create_approval(run_id, "rejected" if decision == "approved" else "approved", "Another Reviewer", "Replay")

    repo.clear_history()


def test_pending_approval_is_protected_from_clear_history():
    repo = Repository(SimpleNamespace(database_url=""))
    _create_pending_run(repo)
    with pytest.raises(ValueError, match="Cannot clear history"):
        repo.clear_history()

    # Isolate the class-level in-memory test store for subsequent tests.
    with repo._memory_lock:
        for run_id, run in list(repo._memory_runs.items()):
            if run.get("status") == "awaiting_approval":
                repo._memory_runs.pop(run_id, None)
