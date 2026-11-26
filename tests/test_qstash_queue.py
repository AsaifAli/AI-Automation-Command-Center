from datetime import UTC, datetime

from app.core.config import Settings
from app.storage.repository import Repository


def _repo() -> tuple[Repository, str]:
    repository = Repository(Settings(_env_file=None))
    run_id = repository.create_pending_run("competitor", owner_id="qstash-queue-owner")
    return repository, run_id


def test_qstash_dispatch_metadata_retains_envelope_and_message_id():
    repo, run_id = _repo()
    envelope = {
        "run_id": run_id,
        "workflow": "competitor",
        "payload": {"competitors": ["AI"]},
        "owner_id": "qstash-queue-owner",
    }

    generation = repo.prepare_qstash_dispatch(run_id, envelope)
    repo.set_qstash_message_id(run_id, generation, "msg_flowpilot_1")

    metadata = repo.get_qstash_dispatch(run_id)
    assert generation == 1
    assert envelope["dispatch_generation"] == 1
    assert metadata["payload"] == envelope
    assert metadata["message_id"] == "msg_flowpilot_1"
    assert metadata["generation"] == 1


def test_qstash_delivery_claim_is_idempotent_and_generation_scoped():
    repo, run_id = _repo()
    envelope = {"run_id": run_id, "dispatch_generation": 1}
    assert repo.prepare_qstash_dispatch(run_id, envelope) == 1

    assert repo.claim_qstash_run(run_id, generation=1) == "claimed"
    assert repo.claim_qstash_run(run_id, generation=1) == "running"
    assert repo.claim_qstash_run(run_id, generation=0) == "superseded"


def test_pause_during_execution_stages_result_for_resume_without_rerunning():
    repo, run_id = _repo()
    envelope = {"run_id": run_id, "dispatch_generation": 1}
    repo.prepare_qstash_dispatch(run_id, envelope)
    assert repo.claim_qstash_run(run_id, generation=1) == "claimed"
    assert repo.pause_run(run_id) is True

    result = {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "completed",
        "output": {"summary": "result produced before pause checkpoint"},
        "evidence": [],
        "duration_ms": 2500,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }
    assert repo.save_paused_result(run_id, result) is True
    assert repo.get_run(run_id, owner_id="qstash-queue-owner")["status"] == "paused"

    assert repo.resume_run(run_id) is True
    pending = repo.take_pending_result(run_id)
    assert pending == result
    repo.save_result(pending)
    resumed = repo.get_run(run_id, owner_id="qstash-queue-owner")
    assert resumed["status"] == "completed"
    assert resumed["output"]["summary"] == result["output"]["summary"]


def test_qstash_failure_callback_is_terminal_and_late_success_cannot_overwrite_it():
    repo, run_id = _repo()
    envelope = {"run_id": run_id, "dispatch_generation": 1}
    repo.prepare_qstash_dispatch(run_id, envelope)
    assert repo.claim_qstash_run(run_id, generation=1) == "claimed"

    failure = {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "failed",
        "output": {"error": "QStash delivery timed out."},
        "evidence": [],
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": ["QStash exhausted delivery time."],
    }
    assert repo.fail_run_if_active(failure) is True
    repo.save_result({
        **failure,
        "status": "completed",
        "output": {"summary": "late result"},
    })
    stored = repo.get_run(run_id, owner_id="qstash-queue-owner")
    assert stored["status"] == "failed"
    assert stored["output"]["error"] == "QStash delivery timed out."


def test_qstash_failure_while_paused_is_committed_when_resumed():
    repo, run_id = _repo()
    envelope = {"run_id": run_id, "dispatch_generation": 1}
    repo.prepare_qstash_dispatch(run_id, envelope)
    assert repo.claim_qstash_run(run_id, generation=1) == "claimed"
    assert repo.pause_run(run_id) is True

    failure = {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "failed",
        "output": {"error": "QStash delivery timed out."},
        "evidence": [],
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": ["QStash exhausted the configured HTTP delivery attempt."],
    }
    assert repo.fail_run_if_active(failure) is True
    assert repo.get_run(run_id, owner_id="qstash-queue-owner")["status"] == "paused"

    late_success = {
        **failure,
        "status": "completed",
        "output": {"summary": "late success"},
    }
    assert repo.save_paused_result(run_id, late_success) is False

    assert repo.resume_run(run_id) is True
    pending = repo.take_pending_result(run_id)
    repo.save_result(pending)
    resumed = repo.get_run(run_id, owner_id="qstash-queue-owner")
    assert resumed["status"] == "failed"
    assert resumed["output"]["error"] == "QStash delivery timed out."


def test_atomic_completion_does_not_lose_result_if_resume_wins_the_checkpoint_race():
    repo, run_id = _repo()
    envelope = {"run_id": run_id}
    repo.prepare_qstash_dispatch(run_id, envelope)
    assert repo.claim_qstash_run(run_id, generation=1) == "claimed"
    assert repo.pause_run(run_id) is True

    # Resume happens after the worker observed a paused state but before it
    # stores the finished result. The atomic completion operation must see
    # that the run is active again and commit the result instead of dropping it.
    assert repo.resume_run(run_id) is True
    result = {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "completed",
        "output": {"summary": "finished exactly once"},
        "evidence": [],
        "duration_ms": 900,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }

    assert repo.complete_qstash_run(result) == "completed"
    stored = repo.get_run(run_id, owner_id="qstash-queue-owner")
    assert stored["status"] == "completed"
    assert stored["output"]["summary"] == "finished exactly once"


def test_atomic_completion_stages_while_paused_and_discards_after_stop():
    repo, run_id = _repo()
    envelope = {"run_id": run_id}
    repo.prepare_qstash_dispatch(run_id, envelope)
    assert repo.claim_qstash_run(run_id, generation=1) == "claimed"
    assert repo.pause_run(run_id) is True
    result = {
        "run_id": run_id,
        "workflow": "competitor",
        "status": "completed",
        "output": {"summary": "paused result"},
        "evidence": [],
        "duration_ms": 900,
        "created_at": datetime.now(UTC),
        "usage": None,
        "warnings": [],
    }

    assert repo.complete_qstash_run(result) == "paused"
    assert repo.stop_run(run_id) is True
    assert repo.complete_qstash_run({
        **result,
        "output": {"summary": "late completion"},
    }) == "cancelled"
