from datetime import UTC, datetime

from app.core.config import Settings
from app.storage.repository import Repository


def _new_repo() -> tuple[Repository, str]:
    settings = Settings(_env_file=None)
    repo = Repository(settings)
    run_id = repo.create_pending_run("competitor", owner_id="owner-controls-test")
    return repo, run_id


def test_pause_and_resume_restore_the_previous_run_state():
    repo, run_id = _new_repo()

    assert repo.pause_run(run_id) is True
    paused = repo.get_run(run_id, owner_id="owner-controls-test")
    assert paused["status"] == "paused"

    assert repo.resume_run(run_id) is True
    resumed = repo.get_run(run_id, owner_id="owner-controls-test")
    assert resumed["status"] == "queued"


def test_pause_running_run_resumes_as_running():
    repo, run_id = _new_repo()
    repo.mark_running(run_id)

    assert repo.pause_run(run_id) is True
    assert repo.resume_run(run_id) is True

    assert repo.get_run(run_id, owner_id="owner-controls-test")["status"] == "running"


def test_stopped_run_cannot_be_resurrected_by_a_late_worker_result():
    repo, run_id = _new_repo()

    assert repo.stop_run(run_id) is True
    repo.mark_running(run_id)
    repo.save_result({
        "run_id": run_id,
        "workflow": "competitor",
        "status": "completed",
        "output": {"summary": "late completion"},
        "evidence": [],
        "duration_ms": 1234,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    })

    stopped = repo.get_run(run_id, owner_id="owner-controls-test")
    assert stopped["status"] == "cancelled"
    assert stopped["output"]["error"] == "Workflow was stopped by the user."


def test_only_active_runs_can_be_paused_or_stopped():
    repo, run_id = _new_repo()
    assert repo.pause_run(run_id) is True
    assert repo.pause_run(run_id) is False
    assert repo.stop_run(run_id) is True
    assert repo.stop_run(run_id) is False
    assert repo.resume_run(run_id) is False
