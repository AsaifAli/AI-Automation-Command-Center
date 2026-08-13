import json
from pathlib import Path

from app.core.config import Settings
from app.integrations.llm import LLMProvider
from app.workflows.orchestrator import WorkflowEngine


def main() -> int:
    cases = json.loads(Path("evals/cases.json").read_text(encoding="utf-8"))
    engine = WorkflowEngine(LLMProvider(Settings(demo_mode=True)))
    passed = 0
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
    print(f"Evaluation score: {passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
