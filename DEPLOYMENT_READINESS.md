# FlowPilot Deployment Readiness

## Previous user-verified local baseline (15 November 2026)

The user ran these checks on the existing project before the final 3.5.0 cleanup edits:

- Python 3.12 compile check: passed.
- Ruff: all checks passed.
- Full automated test suite: 52 passed.
- Workflow evaluation suite: 4/4 passed.
- Docker Compose configuration: valid.
- Local Docker end-to-end smoke test: content, competitor, outreach and KPI workflows completed; approval flow passed; 8/8 recorded runs completed with zero failures.
- Docker service stack: API, UI, PostgreSQL, Redis, worker, scheduler, LiteLLM, Prometheus, Grafana, Jaeger and OTel collector observed running.
- Real LiteLLM inference: successful completion through `portfolio-free`, with provider usage metadata returned.
- Streamlit Gateway import collision: corrected; UI rebuilt and confirmed working.

## Validation performed for this 3.5.0 release candidate

- Python compileall: passed.
- Targeted tests for content generation, formatted reports, UI handoff, queued credential vault/worker, input parsing and bootstrap: 22 passed.
- Broader available test subset, excluding the two API test modules that could not be imported because this validation environment lacks the `redis` Python package: 59 passed.
- YAML parsing: `render.yaml`, `render.production.yaml`, Compose, LiteLLM, Prometheus and OTel configuration parsed successfully.
- Render plan IDs checked against the published Render Blueprint schema enum.
- Ruff, full test collection, LangGraph evaluation suite and Docker build/runtime were not executed in this validation container because Ruff, Redis/RQ, LangGraph, Streamlit and Docker are not installed here. Re-run them in the user's Python 3.12/Docker environment before deployment.

## Current Render demo topology

`render.yaml` defines the existing two-service public demo (FastAPI + Streamlit). It runs with `INLINE_EXECUTION=true` and no Render Postgres/Key Value/worker/scheduler. State is ephemeral. This is suitable for inline BYOK and live inference checks, but not durable queued operations.

## Durable Render topology

`render.production.yaml` is an alternate paid topology. It provisions API, UI, RQ worker, scheduler, Render Postgres and private persistent Key Value. It is intended for queued BYOK with durable encrypted credential storage. It must be reviewed for cost and existing-resource ownership before syncing; it is not automatically applied by a push to the current `render.yaml` Blueprint.

## Remaining production acceptance tests

1. Deploy the shared Portfolio LLM Gateway allowlist change for project slug `flowpilot`.
2. Configure the exact Gateway origin on the UI and `/v1` base URL on the API/worker.
3. Confirm active session status returns `project=flowpilot`, `session_project=portfolio`, and a bounded remaining TTL.
4. Queue a BYOK run against durable Render services and verify the Redis job contains only an opaque credential reference.
5. Verify the credential row is encrypted, expires, and is atomically consumed by one worker.
6. Verify a real model-generated Content Studio result, with provider/model/usage metadata and human-review status.
7. Verify approval/rejection and that no external publish/send occurs automatically.
8. Restart API/worker and verify Postgres history/audit records and queue recovery.
9. Verify monitoring targets, dashboards and traces.

**Status:** the previous user-verified baseline passed local tests and basic real inference. This exact release candidate has 59 passing tests in the available subset; its full API test collection, Ruff, evaluation suite and Docker deployment still need verification in the user's fully provisioned environment. Production BYOK and durable Render acceptance remain pending.
