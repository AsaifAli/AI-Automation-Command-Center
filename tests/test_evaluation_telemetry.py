from types import SimpleNamespace

from app.integrations.evaluation_harness import (
    build_flowpilot_event,
    publish_flowpilot_event,
    publish_flowpilot_evaluation_event,
)


def test_flowpilot_telemetry_contains_only_bounded_numeric_metadata():
    secret_prompt = "private client brief that must never be copied"
    result = {
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a92",
        "workflow": "outreach",
        "status": "awaiting_approval",
        "duration_ms": 1280,
        "warnings": [],
        "usage": {"model": "test-model"},
        "output": {
            "private_input": secret_prompt,
            "candidates": [
                {"name": "Private Person", "message": secret_prompt, "approval_required": True},
                {"name": "Another Person", "message": secret_prompt, "approval_required": False},
            ],
            "approval": {"status": "pending"},
        },
    }

    event = build_flowpilot_event(result)

    assert event is not None
    assert event["project"] == "flowpilot"
    assert event["event_type"] == "run.completed"
    assert event["status"] == "success"
    assert event["workflow"] == "outreach"
    assert event["metrics"]["candidate_count"] == 2
    assert event["metrics"]["approval_guarded_count"] == 1
    assert event["metrics"]["approval_required"] is True
    assert secret_prompt not in str(event)
    assert "output" not in event
    assert "name" not in event


def test_flowpilot_failed_run_maps_to_failed_event():
    event = build_flowpilot_event({
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a93",
        "workflow": "kpi",
        "status": "failed",
        "duration_ms": 45,
        "warnings": ["failed"],
        "output": {"error": "internal diagnostic"},
    })

    assert event is not None
    assert event["event_type"] == "run.failed"
    assert event["status"] == "failed"
    assert event["metrics"]["risk_count"] == 0


def test_telemetry_is_disabled_without_configuration():
    settings = SimpleNamespace(
        evaluation_harness_url="",
        evaluation_harness_ingest_token="",
        app_env="production",
    )
    assert publish_flowpilot_event({
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a94",
        "workflow": "content",
        "status": "completed",
        "output": {},
    }, settings) is False


def test_telemetry_uses_validated_harness_origin_and_daemon_dispatch():
    captured = {}

    class FakeThread:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            captured["started"] = True

    settings = SimpleNamespace(
        evaluation_harness_url="https://ai-engineering-evaluation-harness.onrender.com",
        evaluation_harness_ingest_token="private-ingest-token",
        app_env="production",
    )
    result = {
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a95",
        "workflow": "content",
        "status": "completed",
        "output": {"posts": [{"draft": "not copied"}], "approval": {"status": "pending"}},
    }

    assert publish_flowpilot_event(result, settings, thread_factory=FakeThread) is True
    assert captured["started"] is True
    assert captured["daemon"] is True
    assert captured["args"][0] == "https://ai-engineering-evaluation-harness.onrender.com/api/application-events"
    assert captured["args"][1] == "private-ingest-token"
    assert captured["args"][2]["metrics"]["draft_count"] == 1


def test_telemetry_rejects_untrusted_origin():
    settings = SimpleNamespace(
        evaluation_harness_url="https://attacker.example",
        evaluation_harness_ingest_token="private-ingest-token",
        app_env="production",
    )
    assert publish_flowpilot_event({
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a96",
        "workflow": "kpi",
        "status": "completed",
        "output": {},
    }, settings) is False


def test_flowpilot_deterministic_evaluation_summary_is_reported_without_case_content():
    captured = {}

    class FakeThread:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            captured["started"] = True

    settings = SimpleNamespace(
        evaluation_harness_url="https://ai-engineering-evaluation-harness.onrender.com",
        evaluation_harness_ingest_token="private-ingest-token",
        app_env="production",
    )
    summary = {
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a97",
        "cases": 4,
        "passed": 3,
        "duration_ms": 200,
        "private_case_payload": "do not report this",
    }

    assert publish_flowpilot_evaluation_event(summary, settings, thread_factory=FakeThread) is True
    assert captured["started"] is True
    payload = captured["args"][2]
    assert payload["event_type"] == "evaluation.completed"
    assert payload["status"] == "warning"
    assert payload["workflow"] == "deterministic_replay"
    assert payload["metrics"] == {"cases": 4, "passed": 3, "pass_rate": 0.75}
    assert "private_case_payload" not in str(payload)


def test_flowpilot_evaluation_event_rejects_invalid_counts():
    settings = SimpleNamespace(
        evaluation_harness_url="https://ai-engineering-evaluation-harness.onrender.com",
        evaluation_harness_ingest_token="private-ingest-token",
        app_env="production",
    )
    assert publish_flowpilot_evaluation_event({
        "run_id": "8f8e3bb3-5dbd-47e8-8c1a-c2c0a6f84a98",
        "cases": 2,
        "passed": 3,
    }, settings) is False
