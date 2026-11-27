from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.workers.jobs import execute_run


def make_repository(*, claimed: bool = True):
    repository = Mock()
    repository.mark_running.return_value = claimed
    repository.save_paused_result.return_value = False
    repository.get_run.return_value = {
        "status": "stopped",
        "output": {},
        "evidence": [],
        "duration_ms": 0,
        "warnings": [],
    }
    return repository


def make_engine_result():
    return {
        "workflow": "outreach",
        "status": "completed",
        "output": {"draft_count": 2},
        "evidence": [],
        "duration_ms": 1250,
        "warnings": [],
        "usage": {"total_tokens": 90},
    }


def test_execute_run_reports_completed_result_after_persistence():
    settings = SimpleNamespace()
    repository = make_repository()
    result = make_engine_result()

    with (
        patch("app.workers.jobs.get_settings", return_value=settings),
        patch("app.workers.jobs.Repository", return_value=repository),
        patch("app.workers.jobs.LLMProvider"),
        patch("app.workers.jobs.WorkflowEngine") as workflow_engine,
        patch("app.workers.jobs.report_workflow_event") as report_event,
    ):
        workflow_engine.return_value.run.return_value = result

        returned = execute_run(
            "run-12345678",
            "outreach",
            {"topic": "private topic"},
            use_request_context=True,
        )

    assert returned["status"] == "completed"
    assert returned["run_id"] == "run-12345678"
    repository.save_result.assert_called_once_with(returned)
    report_event.assert_called_once_with(settings, returned)


def test_execute_run_reports_terminal_failure_without_changing_failure_behavior():
    settings = SimpleNamespace()
    repository = make_repository()

    with (
        patch("app.workers.jobs.get_settings", return_value=settings),
        patch("app.workers.jobs.Repository", return_value=repository),
        patch("app.workers.jobs.LLMProvider"),
        patch("app.workers.jobs.WorkflowEngine") as workflow_engine,
        patch("app.workers.jobs.report_workflow_event") as report_event,
    ):
        workflow_engine.return_value.run.side_effect = RuntimeError("private provider failure")

        with pytest.raises(RuntimeError, match="private provider failure"):
            execute_run(
                "run-12345678",
                "outreach",
                {"topic": "private topic"},
                use_request_context=True,
            )

    repository.save_result.assert_called_once()
    reported_settings, reported_result = report_event.call_args.args
    assert reported_settings is settings
    assert reported_result["status"] == "failed"
    assert reported_result["run_id"] == "run-12345678"


def test_execute_run_does_not_report_a_job_that_was_not_claimed():
    settings = SimpleNamespace()
    repository = make_repository(claimed=False)

    with (
        patch("app.workers.jobs.get_settings", return_value=settings),
        patch("app.workers.jobs.Repository", return_value=repository),
        patch("app.workers.jobs.LLMProvider"),
        patch("app.workers.jobs.WorkflowEngine"),
        patch("app.workers.jobs.report_workflow_event") as report_event,
    ):
        returned = execute_run(
            "run-12345678",
            "outreach",
            {},
            use_request_context=True,
        )

    assert returned["status"] == "stopped"
    report_event.assert_not_called()


def test_evaluation_harness_outage_never_changes_workflow_result():
    settings = SimpleNamespace()
    repository = make_repository()
    result = make_engine_result()

    with (
        patch("app.workers.jobs.get_settings", return_value=settings),
        patch("app.workers.jobs.Repository", return_value=repository),
        patch("app.workers.jobs.LLMProvider"),
        patch("app.workers.jobs.WorkflowEngine") as workflow_engine,
        patch("app.workers.jobs.report_workflow_event", side_effect=RuntimeError("harness offline")),
    ):
        workflow_engine.return_value.run.return_value = result

        returned = execute_run(
            "run-12345678",
            "outreach",
            {},
            use_request_context=True,
        )

    assert returned["status"] == "completed"


def test_hard_rq_worker_failure_is_reported_after_failure_is_persisted():
    from app.workers.jobs import record_rq_job_failure

    settings = SimpleNamespace()
    repository = Mock()
    job = SimpleNamespace(args=("run-12345678", "outreach"), id="rq-job-1")

    with (
        patch("app.workers.jobs.get_settings", return_value=settings),
        patch("app.workers.jobs.Repository", return_value=repository),
        patch("app.workers.jobs.report_workflow_event") as report_event,
    ):
        record_rq_job_failure(job, None, RuntimeError, RuntimeError("timeout"), None)

    repository.fail_run_if_active.assert_called_once()
    reported_settings, reported_result = report_event.call_args.args
    assert reported_settings is settings
    assert reported_result["run_id"] == "run-12345678"
    assert reported_result["status"] == "failed"
