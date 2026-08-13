#!/usr/bin/env python3
"""Docker Compose end-to-end smoke test for the public API."""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import requests


WORKFLOWS: list[tuple[str, dict[str, Any]]] = [
    ("content", {"topics": ["AI agents in financial operations"], "channels": ["linkedin", "x", "telegram"], "tone": "executive"}),
    ("competitor", {"competitors": ["Example Protocol", "Example AI Startup"]}),
    ("outreach", {"candidates": [{"name": "Demo Partner", "type": "partner", "context": "AI infrastructure collaboration", "channel": "email"}]}),
    ("kpi", {"updates": [
        {"entity": "Portfolio A", "metric": "weekly progress", "value": "on track", "blocker": "None reported"},
        {"entity": "Portfolio B", "metric": "weekly progress", "value": "delayed", "blocker": "Integration dependency"},
    ]}),
]


def call(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    response = session.request(method, url, timeout=15, **kwargs)
    response.raise_for_status()
    return response


def wait_for_run(session: requests.Session, base: str, run_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = call(session, "GET", f"{base}/api/v1/runs/{run_id}").json()
        if data["status"] not in {"queued", "running"}:
            return data
        time.sleep(1)
    raise TimeoutError(f"Run {run_id} did not finish within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with requests.Session() as session:
        ready = call(session, "GET", f"{base}/ready").json()
        print(f"READY: {ready}")
        completed = 0
        outreach_run_id = None

        for workflow, payload in WORKFLOWS:
            response = call(session, "POST", f"{base}/api/v1/runs", json={"workflow": workflow, "payload": payload})
            queued = response.json()
            run_id = queued["run_id"]
            print(f"QUEUED: {workflow} -> {run_id}")
            result = wait_for_run(session, base, run_id, args.timeout)
            print(f"RESULT: {workflow} -> {result['status']} ({result['duration_ms']} ms)")
            if result["status"] not in {"completed", "completed_with_warnings"}:
                print(f"FAILURE OUTPUT: {result.get('output')}", file=sys.stderr)
                return 1
            assert result["run_id"] == run_id, "run_id changed between queue and completion"
            if workflow == "outreach":
                outreach_run_id = run_id
            completed += 1

        if outreach_run_id:
            approval = call(
                session,
                "POST",
                f"{base}/api/v1/runs/{outreach_run_id}/approval",
                json={"decision": "approved", "reviewer": "Smoke Test", "note": "Automated end-to-end verification"},
            ).json()
            print(f"APPROVAL: {approval['decision']} -> {approval['approval_id']}")

        metrics = call(session, "GET", f"{base}/metrics").json()
        print(f"METRICS: {metrics}")
        assert completed == 4
        assert metrics["workflow_runs_completed"] >= 4
        print("E2E SMOKE TEST PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
