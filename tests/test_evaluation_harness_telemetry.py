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


def test_delivery_uses_project_scoped_token_and_does_not_block_on_non_202():
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
