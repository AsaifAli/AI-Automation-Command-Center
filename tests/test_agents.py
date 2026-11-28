from app.agents.competitor import CompetitorAgent
from app.agents.content import ContentAgent
from app.agents.kpi import KPIAgent
from app.agents.outreach import OutreachAgent
from app.core.config import Settings
from app.integrations.llm import LLMProvider

llm = LLMProvider(Settings(demo_mode=True))


def test_content_generates_per_channel():
    result = ContentAgent(llm).run({"topics": ["AI"], "channels": ["x", "linkedin"]})
    assert result["count"] == 2


def test_competitor_has_actions():
    result = CompetitorAgent(llm).run({"competitors": ["A"]})
    assert result["recommended_actions"]


def test_outreach_requires_approval():
    result = OutreachAgent(llm).run({"candidates": [{"name": "A"}]})
    assert result["candidates"][0]["approval_required"] is True


def test_kpi_detects_blocker():
    result = KPIAgent(llm).run({"updates": [{"entity": "A", "blocker": "blocked"}]})
    assert result["risk_count"] == 1


def test_workflow_engine_preserves_supplied_run_id():
    from app.workflows.orchestrator import WorkflowEngine

    engine = WorkflowEngine(llm)
    result = engine.run("content", {"topics": ["AI"], "channels": ["x"]}, run_id="run-fixed")
    assert result["run_id"] == "run-fixed"


def test_competitor_without_matching_feeds_is_unverified_not_high_priority():
    result = CompetitorAgent(llm).run({"competitors": ["A"], "sources": []})
    assert result["competitors"][0]["priority"] == "unverified"
    assert result["competitors"][0]["observed_items"] == []
    assert "does not establish" in result["competitors"][0]["implication"]


def test_kpi_brief_reports_real_update_and_risk_counts():
    result = KPIAgent(llm).run({
        "updates": [
            {"entity": "A", "metric": "delivery", "value": "delayed", "blocker": "Staffing"},
            {"entity": "B", "metric": "quality", "value": "on track", "blocker": "None"},
        ]
    })
    assert "2 update(s) were reviewed" in result["leadership_brief"]
    assert "1 at risk" in result["leadership_brief"]



def test_workflow_engine_emits_real_step_boundaries_and_ignores_event_sink_failures():
    from app.workflows.orchestrator import WorkflowEngine

    events = []
    engine = WorkflowEngine(llm)
    result = engine.run(
        "kpi",
        {"updates": [{"entity": "A", "metric": "delivery", "value": "on track", "blocker": "None"}]},
        run_id="run-trace",
        on_event=lambda event, detail: events.append((event, detail)),
    )

    assert result["status"] == "completed"
    assert [(event, detail["step_id"]) for event, detail in events] == [
        ("step_started", "execute"),
        ("step_completed", "execute"),
        ("step_started", "validate"),
        ("step_completed", "validate"),
    ]
    assert all(isinstance(detail.get("duration_ms"), int) for event, detail in events if event == "step_completed")

    resilient = engine.run(
        "kpi",
        {"updates": [{"entity": "A", "metric": "delivery", "value": "on track", "blocker": "None"}]},
        run_id="run-trace-resilient",
        on_event=lambda *_args: (_ for _ in ()).throw(RuntimeError("event sink unavailable")),
    )
    assert resilient["status"] == "completed"


def test_workflow_engine_reports_evaluation_for_observed_execute_and_validate_steps(monkeypatch):
    from app.integrations import evaluation_harness
    from app.workflows.orchestrator import WorkflowEngine

    reported = {}
    monkeypatch.setattr(
        evaluation_harness,
        "report_workflow_event",
        lambda settings, result: True,
    )
    monkeypatch.setattr(
        evaluation_harness,
        "report_workflow_evaluation_event",
        lambda settings, result, *, completed_steps: (
            reported.update(
                result_status=result["status"],
                workflow=result["workflow"],
                completed_steps=completed_steps,
            ) or True
        ),
    )

    result = WorkflowEngine(llm).run(
        "kpi",
        {"updates": [{"entity": "A", "metric": "delivery", "value": "on track", "blocker": "None"}]},
        run_id="runtime-evaluation-run",
    )

    assert result["status"] == "completed"
    assert reported == {
        "result_status": "completed",
        "workflow": "kpi",
        "completed_steps": ["execute", "validate"],
    }
