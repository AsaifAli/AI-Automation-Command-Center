from datetime import UTC, datetime

import fakeredis
import pytest

import app.storage.redis_state as redis_state_module
from app.core.config import Settings
from app.storage.repository import Repository


@pytest.fixture
def redis_repository(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=False)
    monkeypatch.setattr(redis_state_module.Redis, "from_url", lambda *_args, **_kwargs: client)
    settings = Settings(
        _env_file=None,
        redis_state_enabled=True,
        redis_url="redis://unused/0",
        redis_key_prefix="aiac-test",
    )
    return Repository(settings), client


def _completed(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "completed",
        "output": {"summary": "persisted output"},
        "evidence": [],
        "duration_ms": 1200,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }


def test_redis_store_persists_runs_across_repository_instances(redis_repository):
    repo, client = redis_repository
    run_id = repo.create_pending_run("competitor", owner_id="owner-a")

    second_repo = Repository(repo.settings)
    loaded = second_repo.get_run(run_id, owner_id="owner-a")

    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded["workflow"] == "competitor"
    assert loaded["created_at"].tzinfo is not None
    assert second_repo.get_run(run_id, owner_id="owner-b") is None
    assert client.exists(f"aiac-test:run:{run_id}") == 1


def test_redis_dispatch_is_atomic_generation_scoped_and_recoverable(redis_repository):
    repo, _client = redis_repository
    run_id = repo.create_pending_run("competitor", owner_id="owner-a")
    envelope = {"run_id": run_id, "workflow": "competitor", "payload": {"q": "AI"}}

    generation = repo.prepare_rq_dispatch(run_id, envelope)
    assert generation == 1
    assert repo.set_rq_job_id(run_id, generation, f"{run_id}-1") is True
    assert repo.claim_rq_run(run_id, generation, f"{run_id}-1") is True
    assert repo.claim_rq_run(run_id, generation, f"{run_id}-1") is True
    assert repo.claim_rq_run(run_id, generation + 1, f"{run_id}-2") is False

    recovered = Repository(repo.settings).get_rq_dispatch(run_id)
    assert recovered["generation"] == 1
    assert recovered["job_id"] == f"{run_id}-1"
    assert recovered["payload"]["payload"] == {"q": "AI"}


def test_redis_pause_holds_completed_result_and_resume_commits_it_once(redis_repository):
    repo, _client = redis_repository
    run_id = repo.create_pending_run("competitor", owner_id="owner-a")
    envelope = {"run_id": run_id, "workflow": "competitor", "payload": {"q": "AI"}}
    repo.prepare_rq_dispatch(run_id, envelope)
    assert repo.pause_run(run_id) is True

    result = _completed(run_id)
    assert repo.save_paused_result(run_id, result) is True
    assert repo.save_paused_result(run_id, result) is False
    assert repo.get_run(run_id, owner_id="owner-a")["status"] == "paused"

    assert repo.resume_run(run_id) is True
    pending = repo.take_pending_result(run_id)
    assert pending["output"]["summary"] == "persisted output"
    repo.save_result(pending)

    loaded = Repository(repo.settings).get_run(run_id, owner_id="owner-a")
    assert loaded["status"] == "completed"
    assert loaded["output"]["summary"] == "persisted output"
    assert repo.take_pending_result(run_id) is None


def test_redis_stop_is_terminal_and_rejects_late_worker_results(redis_repository):
    repo, _client = redis_repository
    run_id = repo.create_pending_run("competitor", owner_id="owner-a")

    assert repo.stop_run(run_id) is True
    repo.mark_running(run_id)
    repo.save_result(_completed(run_id))

    loaded = Repository(repo.settings).get_run(run_id, owner_id="owner-a")
    assert loaded["status"] == "cancelled"
    assert loaded["output"]["error"] == "Workflow was stopped by the user."


def test_redis_gateway_job_credentials_are_single_use_and_expire(redis_repository):
    repo, _client = redis_repository
    run_id = repo.create_pending_run("competitor", owner_id="owner-a")
    credential_id = repo.store_gateway_job_credential(run_id, b"encrypted-bearer", 120)

    assert repo.consume_gateway_job_credential(run_id, credential_id) == b"encrypted-bearer"
    assert repo.consume_gateway_job_credential(run_id, credential_id) is None


def test_redis_approvals_and_metrics_survive_repository_recreation(redis_repository):
    repo, _client = redis_repository
    run_id = repo.create_pending_run("content", owner_id="owner-a")
    repo.mark_running(run_id)
    repo.save_result({
        **_completed(run_id),
        "status": "awaiting_approval",
        "output": {"approval": {"status": "pending", "items": []}},
    })

    second_repo = Repository(repo.settings)
    approvals = second_repo.list_pending_approvals(owner_id="owner-a")
    assert len(approvals) == 1
    assert approvals[0]["run_id"] == run_id

    decision = second_repo.create_approval(run_id, "approved", "Reviewer", "Looks good")
    assert decision["decision"] == "approved"
    metrics = second_repo.metrics()
    assert metrics["workflow_runs_total"] == 1
    assert metrics["workflow_approvals_approved"] == 1


def test_redis_history_clear_keeps_active_runs_and_deletes_terminal_runs(redis_repository):
    repo, _client = redis_repository
    active_id = repo.create_pending_run("competitor", owner_id="owner-a")
    terminal_id = repo.create_pending_run("kpi", owner_id="owner-a")
    repo.save_result(_completed(terminal_id))

    with pytest.raises(ValueError, match="queued, running, or awaiting-approval"):
        repo.clear_history()

    repo.stop_run(active_id)
    assert repo.clear_history() == 2
    assert Repository(repo.settings).list_runs(owner_id="owner-a") == []



def test_redis_state_repository_ping_checks_connection_without_loading_run_history(redis_repository):
    repo, client = redis_repository
    client.set("unrelated:existing-key", "keep")
    assert repo.ping() is True
    assert client.get("unrelated:existing-key") == b"keep"
