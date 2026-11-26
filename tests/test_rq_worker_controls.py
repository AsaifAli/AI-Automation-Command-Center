from datetime import UTC, datetime

from app.core.config import Settings
from app.storage.repository import Repository


def _repo() -> tuple[Repository, str]:
    repository = Repository(Settings(_env_file=None))
    run_id = repository.create_pending_run("competitor", owner_id="rq-controls-owner")
    return repository, run_id


def test_rq_dispatch_metadata_is_persisted_and_generation_scoped():
    repo, run_id = _repo()
    envelope = {
        "run_id": run_id,
        "workflow": "competitor",
        "payload": {"competitors": ["AI"]},
        "owner_id": "rq-controls-owner",
        "gateway_credential_id": "opaque-credential-id",
    }

    generation = repo.prepare_rq_dispatch(run_id, envelope)
    assert repo.set_rq_job_id(run_id, generation, f"{run_id}-{generation}") is True

    dispatch = repo.get_rq_dispatch(run_id)
    assert generation == 1
    assert dispatch["generation"] == 1
    assert dispatch["payload"] == envelope
    assert dispatch["job_id"] == f"{run_id}-1"


def test_rq_dispatch_cannot_be_prepared_for_a_paused_or_terminal_run():
    repo, run_id = _repo()
    assert repo.pause_run(run_id) is True

    try:
        repo.prepare_rq_dispatch(run_id, {"run_id": run_id})
    except ValueError as exc:
        assert "queued" in str(exc)
    else:
        raise AssertionError("A paused run must not receive a new queue generation")


def test_mark_running_is_an_atomic_execution_gate():
    repo, run_id = _repo()

    assert repo.mark_running(run_id) is True
    assert repo.mark_running(run_id) is False
    assert repo.pause_run(run_id) is True
    assert repo.mark_running(run_id) is False

    assert repo.resume_run(run_id) is True
    assert repo.stop_run(run_id) is True
    assert repo.mark_running(run_id) is False


def test_worker_does_not_consume_gateway_credential_when_run_is_paused(monkeypatch):
    from app.workers import jobs

    class PausedRepo:
        def mark_running(self, run_id):
            return False

        def get_run(self, run_id):
            return {"run_id": run_id, "status": "paused"}

        def consume_gateway_job_credential(self, run_id, credential_id):
            raise AssertionError("A paused job must not consume its one-time Gateway credential")

    class UnusedEngine:
        def __init__(self, llm):
            raise AssertionError("A paused job must not construct or execute the workflow engine")

    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(jobs, "Repository", lambda settings: PausedRepo())
    monkeypatch.setattr(jobs, "WorkflowEngine", UnusedEngine)

    result = jobs.execute_run(
        "run-paused-before-start",
        "competitor",
        {"competitors": ["AI"]},
        gateway_credential_id="opaque-credential-id",
    )

    assert result["status"] == "paused"


def test_worker_stages_completion_when_run_is_paused_during_execution(monkeypatch):
    from app.workers import jobs

    staged = []

    class PausedDuringRunRepo:
        def mark_running(self, run_id):
            return True

        def save_paused_result(self, run_id, result):
            staged.append((run_id, result))
            return True

        def save_result(self, result):
            raise AssertionError("A completion for a paused run must be staged, not committed")

    class CompletingEngine:
        def __init__(self, llm):
            pass

        def run(self, workflow, payload, run_id):
            return {
                "workflow": workflow,
                "status": "completed",
                "output": {"summary": "completed after pause"},
                "evidence": [],
                "duration_ms": 123,
                "created_at": datetime.now(UTC),
                "usage": None,
                "warnings": [],
            }

    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(jobs, "Repository", lambda settings: PausedDuringRunRepo())
    monkeypatch.setattr(jobs, "WorkflowEngine", CompletingEngine)
    monkeypatch.setattr(jobs, "LLMProvider", lambda settings: object())

    result = jobs.execute_run("run-paused-mid-flight", "competitor", {})

    assert result["status"] == "completed"
    assert staged[0][0] == "run-paused-mid-flight"
    assert staged[0][1]["output"]["summary"] == "completed after pause"


def test_worker_stages_failure_when_run_is_paused_during_execution(monkeypatch):
    from app.workers import jobs

    staged = []

    class PausedDuringRunRepo:
        def mark_running(self, run_id):
            return True

        def save_paused_result(self, run_id, result):
            staged.append(result)
            return True

        def save_result(self, result):
            raise AssertionError("A failure for a paused run must be staged, not committed")

    class FailingEngine:
        def __init__(self, llm):
            pass

        def run(self, workflow, payload, run_id):
            raise RuntimeError("provider failed")

    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(jobs, "Repository", lambda settings: PausedDuringRunRepo())
    monkeypatch.setattr(jobs, "WorkflowEngine", FailingEngine)
    monkeypatch.setattr(jobs, "LLMProvider", lambda settings: object())

    try:
        jobs.execute_run("run-paused-failure", "competitor", {})
    except RuntimeError as exc:
        assert str(exc) == "provider failed"
    else:
        raise AssertionError("Worker exceptions should remain visible to RQ retry/failure handling")

    assert staged[0]["status"] == "failed"


def test_rq_claim_accepts_a_replay_of_the_same_job_after_worker_recovery():
    repo, run_id = _repo()
    envelope = {
        "run_id": run_id,
        "workflow": "competitor",
        "payload": {},
        "owner_id": "rq-controls-owner",
    }
    generation = repo.prepare_rq_dispatch(run_id, envelope)
    job_id = f"{run_id}-{generation}"
    assert repo.set_rq_job_id(run_id, generation, job_id) is True

    assert repo.claim_rq_run(run_id, generation, job_id) is True
    # RQ can recover and replay the same job after a worker process disappears.
    assert repo.claim_rq_run(run_id, generation, job_id) is True
    # A stale generation or different job cannot take over the current run.
    assert repo.claim_rq_run(run_id, generation, "another-job") is False
    assert repo.claim_rq_run(run_id, generation + 1, job_id) is False


def test_rq_claim_rejects_old_job_after_a_paused_run_is_resumed():
    repo, run_id = _repo()
    first = {"run_id": run_id, "workflow": "competitor", "payload": {}}
    generation = repo.prepare_rq_dispatch(run_id, first)
    old_job_id = f"{run_id}-{generation}"
    repo.set_rq_job_id(run_id, generation, old_job_id)
    assert repo.pause_run(run_id) is True
    assert repo.resume_run(run_id) is True

    second = {"run_id": run_id, "workflow": "competitor", "payload": {}}
    next_generation = repo.prepare_rq_dispatch(run_id, second)
    new_job_id = f"{run_id}-{next_generation}"
    repo.set_rq_job_id(run_id, next_generation, new_job_id)

    assert repo.claim_rq_run(run_id, generation, old_job_id) is False
    assert repo.claim_rq_run(run_id, next_generation, new_job_id) is True


def test_rq_failure_callback_marks_a_timed_out_job_failed(monkeypatch):
    from app.workers import jobs

    captured = {}

    class FailureRepo:
        def __init__(self, settings):
            pass

        def fail_run_if_active(self, result):
            captured["result"] = result
            return True

    class FakeJob:
        id = "rq-job-1"
        args = ("run-rq-timeout", "competitor", {"competitors": ["AI"]})

    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(jobs, "Repository", FailureRepo)

    jobs.record_rq_job_failure(FakeJob(), object(), TimeoutError, TimeoutError("timed out"), None)

    assert captured["result"]["run_id"] == "run-rq-timeout"
    assert captured["result"]["status"] == "failed"
    assert captured["result"]["output"]["failure_code"] == "worker_job_failed"
    assert "timed out" not in repr(captured["result"])


def test_rq_failure_callback_ignores_jobs_without_a_run_reference(monkeypatch):
    from app.workers import jobs

    class FakeJob:
        id = "unexpected-job"
        args = ()

    def unexpected_repo(*args, **kwargs):
        raise AssertionError("A malformed job must not write run state")

    monkeypatch.setattr(jobs, "Repository", unexpected_repo)
    jobs.record_rq_job_failure(FakeJob(), object(), RuntimeError, RuntimeError("x"), None)


def test_stop_discards_a_result_held_while_paused():
    repo, run_id = _repo()
    held = {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "completed",
        "output": {"summary": "should be discarded after Stop"},
        "evidence": [],
        "duration_ms": 5,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }
    assert repo.pause_run(run_id) is True
    assert repo.save_paused_result(run_id, held) is True
    assert repo.stop_run(run_id) is True
    assert repo.get_rq_dispatch(run_id)["pending_result"] is None
    assert repo.resume_run(run_id) is False
