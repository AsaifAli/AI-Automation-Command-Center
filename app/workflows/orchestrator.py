import time
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.competitor import CompetitorAgent
from app.agents.content import ContentAgent
from app.agents.kpi import KPIAgent
from app.agents.outreach import OutreachAgent
from app.integrations.intelligence import IntelligenceSource
from app.integrations.llm import LLMProvider

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:  # pragma: no cover
    trace = None


class State(TypedDict, total=False):
    payload: dict[str, Any]
    output: dict[str, Any]
    warnings: list[str]
    usage: dict[str, Any]


class WorkflowEngine:
    def __init__(self, llm: LLMProvider):
        intelligence = IntelligenceSource(llm.settings)
        self.agents = {
            "content": ContentAgent(llm),
            "competitor": CompetitorAgent(llm, intelligence),
            "outreach": OutreachAgent(llm),
            "kpi": KPIAgent(llm),
        }
        self.llm = llm

    def _graph(self, workflow: str):
        agent = self.agents[workflow]

        def execute(state: State) -> State:
            result = agent.run(state.get("payload", {}))
            usage = result.pop("_usage", None)
            return {"output": result, "usage": usage or {}}

        def validate(state: State) -> State:
            output = state.get("output", {})
            warnings = []
            if not output:
                warnings.append("Agent returned an empty output.")
            if workflow == "outreach" and any(c.get("approval_required") is not True for c in output.get("candidates", [])):
                warnings.append("Outreach policy violation: every candidate must require approval.")
            return {"warnings": warnings}

        graph = StateGraph(State)
        graph.add_node("execute", execute)
        graph.add_node("validate", validate)
        graph.set_entry_point("execute")
        graph.add_edge("execute", "validate")
        graph.add_edge("validate", END)
        return graph.compile()

    def run(self, workflow: str, payload: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        run_id = run_id or str(uuid.uuid4())
        tracer = trace.get_tracer("ai-automation") if trace else None
        span_context = tracer.start_as_current_span(f"workflow:{workflow}") if tracer else None
        if span_context:
            span = span_context.__enter__()
            span.set_attribute("workflow", workflow)
            span.set_attribute("run_id", run_id)
        try:
            state = self._graph(workflow).invoke({"payload": payload})
            status = "completed" if not state.get("warnings") else "completed_with_warnings"
            output = state.get("output", {})
            warnings = state.get("warnings", [])
            usage = dict(state.get("usage") or {})
            # Normalize provider metadata even when an agent returns only token/cost fields.
            usage.setdefault(
                "provider",
                self.llm.settings.llm_provider if not self.llm.settings.demo_mode else "demo",
            )
            usage.setdefault(
                "model",
                self.llm.settings.openai_model if not self.llm.settings.demo_mode else "deterministic-demo",
            )
            usage.setdefault("prompt_tokens", 0)
            usage.setdefault("completion_tokens", 0)
            usage.setdefault("total_tokens", int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0)))
            usage.setdefault("estimated_cost_usd", 0.0)
        except Exception as exc:
            status = "failed"
            output = {"error": str(exc), "recovery": "Inspect worker logs and retry after correcting the input/provider."}
            warnings = ["Workflow execution failed before validation completed."]
            usage = None
            if span_context:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
        finally:
            if span_context:
                span.set_attribute("status", status)
                span_context.__exit__(None, None, None)

        duration_ms = int((time.perf_counter() - started) * 1000)
        now = datetime.now(timezone.utc)
        evidence = [{
            "source": "workflow-engine",
            "title": "Execution trace",
            "detail": f"Workflow {workflow} executed through LangGraph execute → validate stages.",
            "confidence": 1.0,
            "url": None,
            "observed_at": now,
        }]
        return {
            "run_id": run_id,
            "workflow": workflow,
            "status": status,
            "output": output,
            "evidence": evidence,
            "duration_ms": duration_ms,
            "created_at": now,
            "usage": usage,
            "warnings": warnings,
        }
