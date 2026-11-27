import json
import time
import uuid
from pathlib import Path

from app.core.config import Settings
from app.integrations.evaluation_harness import publish_flowpilot_evaluation_event
from app.integrations.llm import LLMProvider
from app.workflows.orchestrator import WorkflowEngine


def main() -> int:
    cases = json.loads(Path("evals/cases.json").read_text(encoding="utf-8"))
    settings = Settings(demo_mode=True)
    engine = WorkflowEngine(LLMProvider(settings))
    passed = 0
    started = time.perf_counter()
    for case in cases:
        result = engine.run(case["workflow"], case["payload"])
        output = result["output"]
        assertions = case["assertions"]
        ok = True
        if "count" in assertions:
            ok &= output.get("count") == assertions["count"]
        if "has_actions" in assertions:
            ok &= bool(output.get("recommended_actions")) == assertions["has_actions"]
        if "approval_required" in assertions:
            ok &= all(c.get("approval_required") is assertions["approval_required"] for c in output.get("candidates", []))
        if "risk_count" in assertions:
            ok &= output.get("risk_count") == assertions["risk_count"]
        print(f"{'PASS' if ok else 'FAIL'}  {case['name']}")
        passed += int(ok)

    duration_ms = round((time.perf_counter() - started) * 1000)
    print(f"Evaluation score: {passed}/{len(cases)}")
    # Report only aggregate counts and timing. Workflow inputs and outputs are
    # deliberately excluded from the Evaluation Harness event payload.
    publish_flowpilot_evaluation_event(
        {
            "run_id": uuid.uuid4().hex,
            "cases": len(cases),
            "passed": passed,
            "duration_ms": duration_ms,
        },
        settings,
    )
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
