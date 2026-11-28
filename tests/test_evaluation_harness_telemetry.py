from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.integrations.evaluation_harness import (
    build_flowpilot_event,
    report_workflow_event,
)


def sample_result():
    return {
        "run_id": "d84b8c9a-1a4b-4a55-90d8-b4a0f1234567",
        "workflow": "outreach",
        "status": "awaiting_approval",
        "duration_ms": 1234,
        "output": {
            "approval": {"status": "pending"},
            "message": "PRIVATE GENERATED MESSAGE MUST NOT BE SENT",
        },
        "evidence": [{"detail": "PRIVATE EVIDENCE MUST NOT BE SENT"}],
        "warnings": [],
        "usage": {
            "provider": "openrouter",
            "model": "test-model",
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
            "cost_estimate_available": False,
        },
    }


def test_event_contract_contains_only_bounded_metadata():
    event = build_flowpilot_event(sample_result())

    assert event["project"] == "flowpilot"
    assert event["event_type"] == "run.completed"
    assert event["status"] == "warning"
    assert event["workflow"] == "outreach"
    assert event["event_id"] == sample_result()["run_id"]
    assert event["duration_ms"] == 1234
    assert event["metrics"]["approval_pending"] is True
    assert event["metrics"]["total_tokens"] == 200
    serialized = str(event)
    assert "PRIVATE GENERATED MESSAGE" not in serialized
    assert "PRIVATE EVIDENCE" not in serialized
    assert "output" not in event
    assert "evidence" not in event


def test_failed_workflow_is_reported_as_failure():
    result = sample_result()
    result["status"] = "failed"
    event = build_flowpilot_event(result)
    assert event["event_type"] == "run.failed"
    assert event["status"] == "failed"


def test_missing_configuration_disables_reporting_without_network_call():
    settings = SimpleNamespace(evaluation_harness_url="", evaluation_harness_ingest_token="", app_env="test")
    with patch("app.integrations.evaluation_harness.httpx.post") as post:
        assert report_workflow_event(settings, sample_result()) is False
        post.assert_not_called()


def test_delivery_uses_project_scoped_token():
    settings = SimpleNamespace(
        evaluation_harness_url="https://ai-engineering-evaluation-harness.onrender.com",
        evaluation_harness_ingest_token="flowpilot-secret",
        app_env="production",
    )
    response = Mock(status_code=202)
    with patch("app.integrations.evaluation_harness.httpx.post", return_value=response) as post:
        assert report_workflow_event(settings, sample_result()) is True
    args, kwargs = post.call_args
    assert args[0] == "https://ai-engineering-evaluation-harness.onrender.com/api/application-events"
    assert kwargs["headers"]["Authorization"] == "Bearer flowpilot-secret"
    assert kwargs["json"]["project"] == "flowpilot"
    assert kwargs["timeout"] == 1.5


def test_approved_action_is_not_reported_as_pending():
    result = sample_result()
    result["status"] = "completed"
    result["output"]["approval"]["status"] = "approved"
    event = build_flowpilot_event(result)
    assert event["metrics"]["approval_pending"] is False


def test_non_202_harness_response_fails_open_for_the_workflow():
    settings = SimpleNamespace(
        evaluation_harness_url="https://ai-engineering-evaluation-harness.onrender.com",
        evaluation_harness_ingest_token="flowpilot-secret",
        app_env="production",
    )
    with patch(
        "app.integrations.evaluation_harness.httpx.post",
        return_value=Mock(status_code=503),
    ):
        assert report_workflow_event(settings, sample_result()) is False


def test_untrusted_http_endpoint_is_not_contacted_in_production():
    settings = SimpleNamespace(
        evaluation_harness_url="http://localhost:8000",
        evaluation_harness_ingest_token="flowpilot-secret",
        app_env="production",
    )
    with patch("app.integrations.evaluation_harness.httpx.post") as post:
        assert report_workflow_event(settings, sample_result()) is False
        post.assert_not_called()


def test_evaluation_event_reports_real_stages_and_approval_without_content(monkeypatch):
    from app.integrations.evaluation_harness import build_flowpilot_evaluation_event

    monkeypatch.setenv("EVALUATION_EXPERIMENT_ID", "flowpilot-run-v1")
    monkeypatch.setenv("EVALUATION_DATASET_VERSION", "workflow-fixtures-v2")
    monkeypatch.setenv("EVALUATION_PROMPT_VERSION", "agent-prompts-v3")
    monkeypatch.setenv("EVALUATION_CONFIG_VERSION", "workflow-config-v4")
    result = sample_result()
    result["evidence"] = [{"source": "workflow-engine", "detail": "private run detail"}]
    result["output"]["approval"] = {"status": "pending"}
    event = build_flowpilot_evaluation_event(
        result,
        completed_steps=["execute", "validate"],
    )

    assert event["project"] == "flowpilot"
    assert event["event_type"] == "evaluation.completed"
    assert event["status"] == "success"
    assert event["event_id"].startswith("eval-")
    assert len(event["event_id"]) == 37
    assert event["experiment_id"] == "flowpilot-run-v1"
    assert event["dataset_version"] == "workflow-fixtures-v2"
    assert event["prompt_version"] == "agent-prompts-v3"
    assert event["config_version"] == "workflow-config-v4"
    assert event["metrics"]["runtime.workflow_completion"] == 1.0
    assert event["metrics"]["runtime.step_coverage"] == 1.0
    assert event["metrics"]["runtime.validation_completed"] == 1.0
    assert event["metrics"]["runtime.approval_compliance"] == 1.0
    assert event["metrics"]["runtime.audit_traceability"] == 1.0
    serialized = str(event)
    assert "PRIVATE GENERATED MESSAGE" not in serialized
    assert "PRIVATE EVIDENCE" not in serialized
    assert "private run detail" not in serialized
    assert "output" not in event
    assert "evidence" not in event


def test_evaluation_event_penalizes_missing_validation_and_failed_workflow():
    from app.integrations.evaluation_harness import build_flowpilot_evaluation_event

    result = sample_result()
    result["status"] = "failed"
    result["output"] = {"failure_code": "workflow_execution_error"}
    event = build_flowpilot_evaluation_event(result, completed_steps=["execute"])

    assert event["metrics"]["runtime.workflow_completion"] == 0.0
    assert event["metrics"]["runtime.step_coverage"] == 0.5
    assert event["metrics"]["runtime.validation_completed"] == 0.0


def test_evaluation_report_uses_same_project_token_and_fails_open():
    from app.integrations.evaluation_harness import report_workflow_evaluation_event

    settings = SimpleNamespace(
        evaluation_harness_url="https://ai-engineering-evaluation-harness.onrender.com",
        evaluation_harness_ingest_token="flowpilot-secret",
        app_env="production",
    )
    response = Mock(status_code=202)
    with patch(
        "app.integrations.evaluation_harness.httpx.post",
        return_value=response,
    ) as post:
        accepted = report_workflow_evaluation_event(
            settings,
            sample_result(),
            completed_steps=["execute", "validate"],
        )

    assert accepted is True
    args, kwargs = post.call_args
    assert args[0] == "https://ai-engineering-evaluation-harness.onrender.com/api/application-events"
    assert kwargs["headers"]["Authorization"] == "Bearer flowpilot-secret"
    assert kwargs["json"]["event_type"] == "evaluation.completed"
    assert kwargs["json"]["metrics"]["runtime.step_coverage"] == 1.0
    assert kwargs["timeout"] == 1.5
