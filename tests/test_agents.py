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
