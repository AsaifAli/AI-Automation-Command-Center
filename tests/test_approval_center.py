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
        if workflow == "content":
            assert result["output"]["approval"]["items"][0]["approval_status"] == "pending"
            assert result["output"]["approval"]["pending_count"] == 1


def test_analysis_only_workflow_does_not_create_an_approval_gate():
    result = _engine("kpi", {"normalized_updates": [], "_usage": {}}).run("kpi", {}, run_id="test-kpi")
    assert result["status"] == "completed"
    assert "approval" not in result["output"]


def _create_pending_run(repo: Repository, workflow: str = "content", draft_count: int = 1) -> str:
    run_id = repo.create_pending_run(workflow)
    repo.mark_running(run_id)
    now = datetime.now(UTC)
    repo.save_result(
        {
            "run_id": run_id,
            "workflow": workflow,
            "status": "awaiting_approval",
            "output": {
                "posts": [
                    {"topic": "AI operations", "channel": f"channel-{index}", "draft": f"Draft {index}"}
                    for index in range(draft_count)
                ],
                "approval": {
                    "status": "pending",
                    "action": "publish_content",
                    "title": "Review content",
                    "summary": f"{draft_count} drafts are ready for review.",
                    "items": [
                        {"topic": "AI operations", "channel": f"channel-{index}", "draft": f"Draft {index}", "approval_status": "pending"}
                        for index in range(draft_count)
                    ],
                    "approved_count": 0,
                    "rejected_count": 0,
                    "pending_count": draft_count,
                    "total_count": draft_count,
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
    [("approved", "completed"), ("rejected", "rejected")],
)
def test_approval_decision_is_persisted_once_and_is_not_external_execution(decision, expected_status):
    repo = Repository(SimpleNamespace(database_url=""))
    run_id = _create_pending_run(repo)

    pending = repo.list_pending_approvals()
    assert any(item["run_id"] == run_id for item in pending)

    recorded = repo.create_approval(run_id, decision, "Authenticated Reviewer", "Reviewed the draft", item_index=0)
    assert recorded["decision"] == decision

    run = repo.get_run(run_id)
    assert run["status"] == expected_status
    assert run["output"]["approval"]["status"] == ("approved" if decision == "approved" else "rejected")
    assert run["output"]["approval"]["items"][0]["approval_status"] == decision
    assert run["output"]["approval"]["execution_status"] == "not_configured"
    assert not any(item["run_id"] == run_id for item in repo.list_pending_approvals())

    with pytest.raises(ValueError, match="no longer awaiting approval"):
        repo.create_approval(run_id, "rejected" if decision == "approved" else "approved", "Another Reviewer", "Replay", item_index=0)

    repo.clear_history()



def test_content_workflow_stays_pending_until_every_draft_is_individually_approved():
    repo = Repository(SimpleNamespace(database_url=""))
    run_id = _create_pending_run(repo, draft_count=3)

    with pytest.raises(ValueError, match="approved individually"):
        repo.create_approval(run_id, "approved", "Reviewer", "Approve all")
    with pytest.raises(ValueError, match="out of range"):
        repo.create_approval(run_id, "approved", "Reviewer", "Invalid draft", item_index=3)

    first = repo.create_approval(run_id, "approved", "Reviewer A", "Looks good", item_index=0)
    run = repo.get_run(run_id)
    approval = run["output"]["approval"]
    assert first["item_index"] == 0
    assert run["status"] == "awaiting_approval"
    assert approval["status"] == "pending"
    assert (approval["approved_count"], approval["pending_count"]) == (1, 2)
    assert approval["items"][0]["approval_status"] == "approved"
    assert approval["items"][1]["approval_status"] == "pending"
    assert run["output"]["posts"][0]["approval_status"] == "approved"
    assert any(item["run_id"] == run_id for item in repo.list_pending_approvals())
    with pytest.raises(ValueError, match="already been reviewed"):
        repo.create_approval(run_id, "rejected", "Reviewer A", "Conflicting decision", item_index=0)

    repo.create_approval(run_id, "approved", "Reviewer B", "Looks good", item_index=1)
    still_pending = repo.get_run(run_id)
    assert still_pending["status"] == "awaiting_approval"
    assert still_pending["output"]["approval"]["pending_count"] == 1

    repo.create_approval(run_id, "approved", "Reviewer C", "Looks good", item_index=2)
    completed = repo.get_run(run_id)
    assert completed["status"] == "completed"
    assert completed["output"]["approval"]["status"] == "approved"
    assert completed["output"]["approval"]["approved_count"] == 3
    assert completed["output"]["approval"]["pending_count"] == 0
    assert completed["output"]["approval"]["execution_status"] == "not_configured"
    assert not any(item["run_id"] == run_id for item in repo.list_pending_approvals())

    with pytest.raises(ValueError, match="no longer awaiting approval"):
        repo.create_approval(run_id, "rejected", "Reviewer D", "Replay", item_index=2)

    repo.clear_history()


def test_content_rejection_is_recorded_per_draft_and_closes_after_remaining_decisions():
    repo = Repository(SimpleNamespace(database_url=""))
    run_id = _create_pending_run(repo, draft_count=2)

    repo.create_approval(run_id, "rejected", "Reviewer A", "Needs rewrite", item_index=0)
    partial = repo.get_run(run_id)
    assert partial["status"] == "awaiting_approval"
    assert partial["output"]["approval"]["rejected_count"] == 1
    assert partial["output"]["approval"]["pending_count"] == 1

    repo.create_approval(run_id, "approved", "Reviewer B", "Other draft is good", item_index=1)
    resolved = repo.get_run(run_id)
    assert resolved["status"] == "rejected"
    assert resolved["output"]["approval"]["status"] == "rejected"
    assert resolved["output"]["approval"]["items"][0]["approval_status"] == "rejected"
    assert resolved["output"]["approval"]["items"][1]["approval_status"] == "approved"
    assert not any(item["run_id"] == run_id for item in repo.list_pending_approvals())

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
