#!/usr/bin/env python3
"""Authenticated Docker Compose end-to-end smoke test for FlowPilot."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS: list[tuple[str, dict[str, Any]]] = [
    (
        "content",
        {
            "topics": ["AI agents in financial operations"],
            "channels": ["linkedin", "x", "telegram"],
            "tone": "executive",
        },
    ),
    (
        "competitor",
        {
            "competitors": ["Example Protocol", "Example AI Startup"]
        },
    ),
    (
        "outreach",
        {
            "candidates": [
                {
                    "name": "Demo Partner",
                    "type": "partner",
                    "context": "AI infrastructure collaboration",
                    "channel": "email",
                }
            ]
        },
    ),
    (
        "kpi",
        {
            "updates": [
                {
                    "entity": "Portfolio A",
                    "metric": "weekly progress",
                    "value": "on track",
                    "blocker": "None reported",
                },
                {
                    "entity": "Portfolio B",
                    "metric": "weekly progress",
                    "value": "delayed",
                    "blocker": "Integration dependency",
                },
            ]
        },
    ),
]


def _dotenv_value(raw_value: str) -> str:
    """Parse the simple scalar values used for FlowPilot's generated .env keys."""
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
        if raw_value.strip().startswith('"'):
            value = (
                value.replace("\\\\", "\\")
                .replace('\\"', '"')
                .replace("\\n", "\n")
            )
        return value
    # Generated credentials contain no spaces or inline comments. Supporting
    # the conventional " # comment" form keeps hand-edited .env files usable.
    return value.split(" #", maxsplit=1)[0].rstrip()


def load_env_file(env_file: Path) -> None:
    """Load missing environment values from .env without printing secrets.

    Existing process environment values take precedence, matching Compose and
    pydantic-settings behavior. Only KEY=VALUE and `export KEY=VALUE` lines
    are needed for the credential fields used by this smoke test.
    """
    if not env_file.is_file():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        if "=" not in stripped:
            continue

        key, raw_value = stripped.split("=", maxsplit=1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if key not in os.environ:
            os.environ[key] = _dotenv_value(raw_value)


def _first_configured_key(*names: str) -> str:
    for name in names:
        raw_value = os.getenv(name, "")
        for candidate in raw_value.split(","):
            candidate = candidate.strip()
            if candidate:
                return candidate
    return ""


def get_smoke_test_credentials() -> tuple[str, str]:
    """Return (operator, approver) credentials accepted by the API service.

    Admin credentials can perform both operations. Otherwise, the workflow
    calls use an operator key and the outreach approval uses a reviewer key.
    Legacy API_KEY remains an admin credential.
    """
    admin_key = _first_configured_key("API_ADMIN_KEYS", "API_KEY")
    operator_key = _first_configured_key("API_OPERATOR_KEYS") or admin_key
    approver_key = _first_configured_key("API_REVIEWER_KEYS") or admin_key
    return operator_key, approver_key


def call(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    response = session.request(
        method,
        url,
        timeout=15,
        **kwargs,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = (response.text or "").strip().replace("\n", " ")[:400]
        message = f"{method} {url} returned HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise requests.HTTPError(message, response=response) from exc
    return response


def wait_for_run(
    session: requests.Session,
    base: str,
    run_id: str,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout

    while time.time() < deadline:
        data = call(
            session,
            "GET",
            f"{base}/api/v1/runs/{run_id}",
        ).json()

        if data["status"] not in {"queued", "running"}:
            return data

        time.sleep(1)

    raise TimeoutError(f"Run {run_id} did not finish within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run authenticated API, worker, LLM and approval smoke tests."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Published FlowPilot API URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Maximum seconds to wait for each workflow (default: 90)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="Environment file containing API role keys (default: .env at repo root)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    load_env_file(args.env_file)
    operator_key, approver_key = get_smoke_test_credentials()

    if not operator_key:
        print(
            "ERROR: No API operator/admin key found. Set API_OPERATOR_KEYS, "
            "API_ADMIN_KEYS, or legacy API_KEY in .env.",
            file=sys.stderr,
        )
        return 2
    if not approver_key:
        print(
            "ERROR: No API reviewer/admin key found. Set API_REVIEWER_KEYS, "
            "API_ADMIN_KEYS, or legacy API_KEY in .env to test outreach approval.",
            file=sys.stderr,
        )
        return 2

    with requests.Session() as operator_session, requests.Session() as approval_session:
        operator_session.headers.update({"X-API-Key": operator_key})
        approval_session.headers.update({"X-API-Key": approver_key})

        ready = call(
            operator_session,
            "GET",
            f"{base}/ready",
        ).json()
        print(f"READY: {ready}")

        completed = 0
        outreach_run_id: str | None = None

        for workflow, payload in WORKFLOWS:
            queued = call(
                operator_session,
                "POST",
                f"{base}/api/v1/runs",
                json={"workflow": workflow, "payload": payload},
            ).json()

            run_id = queued["run_id"]
            print(f"QUEUED: {workflow} -> {run_id}")

            result = wait_for_run(
                operator_session,
                base,
                run_id,
                args.timeout,
            )
            print(
                f"RESULT: {workflow} -> {result['status']} "
                f"({result['duration_ms']} ms)"
            )

            if result["status"] not in {"completed", "completed_with_warnings"}:
                print(
                    f"FAILURE OUTPUT: {result.get('output')}",
                    file=sys.stderr,
                )
                return 1

            if result["run_id"] != run_id:
                raise AssertionError("run_id changed between queue and completion")

            if workflow == "outreach":
                outreach_run_id = run_id
            completed += 1

        if outreach_run_id:
            approval = call(
                approval_session,
                "POST",
                f"{base}/api/v1/runs/{outreach_run_id}/approval",
                json={
                    "decision": "approved",
                    "note": "Automated end-to-end verification",
                },
            ).json()
            print(
                f"APPROVAL: {approval['decision']} -> {approval['approval_id']}"
            )

        metrics = call(
            operator_session,
            "GET",
            f"{base}/metrics",
        ).json()
        print(f"METRICS: {metrics}")

        assert completed == len(WORKFLOWS)
        assert metrics["workflow_runs_completed"] >= len(WORKFLOWS)
        print("E2E SMOKE TEST PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
