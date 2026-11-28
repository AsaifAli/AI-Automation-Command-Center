import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.competitor import CompetitorAgent
from app.agents.content import ContentAgent, ContentGenerationError
from app.agents.kpi import KPIAgent
from app.agents.outreach import OutreachAgent, OutreachGenerationError
from app.integrations.intelligence import IntelligenceSource
from app.integrations.llm import LLMProvider, LLMProviderError

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:  # pragma: no cover
    trace = None


logger = logging.getLogger(__name__)


def _approval_request(workflow: str, output: dict[str, Any], created_at: datetime) -> dict[str, Any] | None:
    """Build a review request for workflows that stage external-impact actions."""
    if workflow == "content" and output.get("posts"):
        posts = output.get("posts", [])
        return {
            "status": "pending",
            "action": "publish_content",
            "title": "Review and authorize content publishing",
            "summary": f"{len(posts)} channel-specific content draft(s) are ready for approval.",
            "items": [
                {
                    "topic": post.get("topic"),
                    "channel": post.get("channel"),
                    "draft": post.get("draft"),
                    "approval_status": "pending",
                }
                for post in posts
            ],
            "approved_count": 0,
            "rejected_count": 0,
            "pending_count": len(posts),
            "total_count": len(posts),
            "created_at": created_at.isoformat(),
        }
    if workflow == "outreach" and output.get("candidates"):
        candidates = output.get("candidates", [])
        return {
            "status": "pending",
            "action": "send_outreach",
            "title": "Review and authorize outreach",
            "summary": f"{len(candidates)} outreach message(s) are ready for approval.",
            "items": [
                {
                    "name": candidate.get("name"),
                    "channel": candidate.get("channel"),
                    "message": candidate.get("message"),
                    "reason": candidate.get("reason"),
                }
                for candidate in candidates
            ],
            "created_at": created_at.isoformat(),
        }
    return None


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

    def _graph(self, workflow: str, on_event: Callable[[str, dict[str, Any]], None] | None = None):
        agent = self.agents[workflow]
        labels = {
            "content": "Prepare channel-specific drafts",
            "competitor": "Analyse competitor signals",
            "outreach": "Qualify and draft outreach",
            "kpi": "Normalize operating updates",
        }

        def emit(event: str, detail: dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                on_event(event, detail)
            except Exception:
                logger.debug("workflow_event_sink_failed event=%s", event, exc_info=True)

        def execute(state: State) -> State:
            started = time.perf_counter()
            label = labels.get(workflow, "Execute workflow")
            emit("step_started", {
                "step_id": "execute", "label": label,
                "summary": "The selected workflow agent is processing the submitted inputs.",
            })
            try:
                result = agent.run(state.get("payload", {}))
            except Exception:
                emit("step_failed", {
                    "step_id": "execute", "label": label,
                    "summary": "The workflow agent could not complete this stage.",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                })
                raise
            usage = result.pop("_usage", None)
            result_count = sum(len(value) for value in result.values() if isinstance(value, list))
            emit("step_completed", {
                "step_id": "execute", "label": label,
                "summary": "The workflow agent returned its result for validation.",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "result_count": result_count,
            })
            return {"output": result, "usage": usage or {}}

        def validate(state: State) -> State:
            started = time.perf_counter()
            emit("step_started", {
                "step_id": "validate", "label": "Verify output and approval policy",
                "summary": "Checking the result against workflow validation and review rules.",
            })
            try:
                output = state.get("output", {})
                warnings = []
                if not output:
                    warnings.append("Agent returned an empty output.")
                if workflow == "outreach" and any(c.get("approval_required") is not True for c in output.get("candidates", [])):
                    warnings.append("Outreach policy violation: every candidate must require approval.")
            except Exception:
                emit("step_failed", {
                    "step_id": "validate", "label": "Verify output and approval policy",
                    "summary": "The output validation stage could not complete.",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                })
                raise
            emit("step_completed", {
                "step_id": "validate", "label": "Verify output and approval policy",
                "summary": "Output validation completed; approval checkpoints remain enforced.",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "warning_count": len(warnings),
            })
            return {"warnings": warnings}

        graph = StateGraph(State)
        graph.add_node("execute", execute)
        graph.add_node("validate", validate)
        graph.set_entry_point("execute")
        graph.add_edge("execute", "validate")
        graph.add_edge("validate", END)
        return graph.compile()

    def run(
        self, workflow: str, payload: dict[str, Any], run_id: str | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        run_id = run_id or str(uuid.uuid4())
        tracer = trace.get_tracer("ai-automation") if trace else None
        span_context = tracer.start_as_current_span(f"workflow:{workflow}") if tracer else None
        if span_context:
            span = span_context.__enter__()
            span.set_attribute("workflow", workflow)
            span.set_attribute("run_id", run_id)
        completed_steps: set[str] = set()

        def capture_step_event(event: str, detail: dict[str, Any]) -> None:
            if event == "step_completed" and isinstance(detail, dict):
                step_id = detail.get("step_id")
                if step_id in {"execute", "validate"}:
                    completed_steps.add(step_id)
            if on_event is not None:
                on_event(event, detail)

        try:
            state = self._graph(workflow, on_event=capture_step_event).invoke({"payload": payload})
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
            usage.setdefault(
                "cost_estimate_available",
                not self.llm.settings.demo_mode
                and not getattr(self.llm, "gateway_session_active", False),
            )
            approval = _approval_request(workflow, output, datetime.now(UTC))
            if approval and status in {"completed", "completed_with_warnings"}:
                output["approval"] = approval
                # This is an approval checkpoint, not an external execution.
                status = "awaiting_approval"
        except Exception as exc:
            status = "failed"
            failure_code = "workflow_execution_error"
            failure_reason = None
            recovery = "Retry the workflow. If the issue persists, share its run reference with an administrator."

            if isinstance(exc, LLMProviderError):
                failure_code = exc.failure_code
                failure_reason = exc.public_message
                recovery = (
                    "Check the configured BYOK session or service provider, model endpoint, and provider availability. "
                    "Then start a new run."
                )
            elif isinstance(exc, (ContentGenerationError, OutreachGenerationError)):
                failure_code = "invalid_model_output"
                failure_reason = str(exc)[:280]
                recovery = "Retry with a shorter or clearer brief. The workflow will validate the regenerated output before returning it."

            logger.exception(
                "workflow_execution_failed workflow=%s run_id=%s failure_code=%s error_type=%s",
                workflow,
                run_id,
                failure_code,
                type(exc).__name__,
            )
            output = {
                "error": "This workflow could not be completed.",
                "failure_code": failure_code,
                "recovery": recovery,
            }
            if failure_reason:
                output["failure_reason"] = failure_reason
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
        now = datetime.now(UTC)
        evidence = [{
            "source": "workflow-engine",
            "title": "Execution trace",
            "detail": f"Workflow {workflow} executed through LangGraph execute → validate stages.",
            "confidence": 1.0,
            "url": None,
            "observed_at": now,
        }]
        result = {
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
        # Telemetry is strictly best-effort. It carries only bounded metadata;
        # a harness outage must never change the workflow result or approval state.
        try:
            from app.integrations.evaluation_harness import report_workflow_event

            report_workflow_event(self.llm.settings, result)
        except Exception:  # noqa: BLE001 - telemetry must never fail a workflow.
            logger.warning("evaluation_harness_reporting_skipped workflow=%s run_id=%s", workflow, run_id)

        try:
            from app.integrations.evaluation_harness import report_workflow_evaluation_event

            report_workflow_evaluation_event(
                self.llm.settings,
                result,
                completed_steps=sorted(completed_steps),
            )
        except Exception:  # noqa: BLE001 - runtime scoring must never fail a workflow.
            logger.warning("evaluation_harness_scoring_skipped workflow=%s run_id=%s", workflow, run_id)
        return result
