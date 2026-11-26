# FlowPilot — AI Workflow Automation

> An AI workflow automation workspace for launching, tracking, and reviewing bounded AI-assisted workflows.

FlowPilot combines a Streamlit operator workspace with a FastAPI workflow control plane. It is designed around explicit workflow definitions, asynchronous execution, role-scoped access, observable run state, and human approval boundaries for external-impact actions.

## What FlowPilot does

The current workspace exposes four workflow types:

| Workflow | Purpose | Typical output |
|---|---|---|
| Content Agent | Turn a topic or brief into channel-aware content drafts | Multi-channel draft for human review |
| Competitor Intelligence | Process configured RSS/Atom sources and synthesize observed signals | Evidence-oriented intelligence brief |
| Outreach Agent | Qualify an opportunity and prepare contextual outreach | Approval-ready draft; sending is not automatic |
| KPI & Progress Tracker | Normalize updates and surface blockers | Leadership progress briefing |

## Application workflow

1. The operator selects a workflow and provides its bounded input.
2. The UI submits an authenticated request to the FastAPI service.
3. The API creates a run reference and schedules execution.
4. Workflow execution records progress and a terminal result.
5. The workspace polls the run and presents status, output, and review context.
6. Actions with external impact remain subject to approval and role permissions.

## Architecture

- **Frontend:** Streamlit operator workspace (ui/app.py).
- **API:** FastAPI control plane (app/api/main.py).
- **Execution:** the no-new-service Free Render topology enqueues RQ jobs to the existing Upstash Redis database. A supervised SimpleWorker child process runs workflows separately from the API request loop.
- **State:** owner-scoped run records, dispatch metadata, approval state, and short-lived encrypted Gateway job credentials are stored in the same Redis database under a dedicated key prefix. PostgreSQL remains an optional backend.
- **LLM access:** portfolio BYOK uses a short-lived, project-scoped Gateway session; raw provider credentials are not put in RQ job arguments.
- **Observability:** health, run metrics, structured logs, and optional OpenTelemetry instrumentation.

## BYOK and Gateway handoff

When FlowPilot is launched from the portfolio website, the website owns provider selection and the user-supplied provider credential. FlowPilot receives a single-use handoff code, removes it from the URL, exchanges it server-side, and keeps the short-lived project token in Streamlit session state. The API validates the token for the FlowPilot project before using it for inference.

Do not put provider API keys or reusable Gateway bearer tokens in source control, URL parameters, or browser storage.

## Deployment

The root render.yaml defines the current **no-new-service Free-tier topology**:

- ai-automation-api — FastAPI, Redis-backed run state, RQ enqueueing, and a supervised embedded worker child process in the same existing Free web service.
- ai-automation-ui — Streamlit workspace.

This topology does not create a Render Background Worker, Render Key Value, Render Postgres, or a second Upstash database. It reuses the existing Free Upstash Redis database. The API parent remains available to serve HTTP while the isolated child process consumes workflow jobs.

### Free-tier RQ worker configuration

Configure these variables on ai-automation-api:

- REDIS_URL — native TLS Redis connection URL, `rediss://default:<password>@<host>:6379/0`. Use the Upstash TCP endpoint and password, not the Upstash REST URL/token.
- REDIS_STATE_ENABLED=true — stores run history and control state in Redis when DATABASE_URL is blank.
- REDIS_KEY_PREFIX=aiac-flowpilot — isolates FlowPilot state keys from other applications sharing the Redis database.
- QUEUE_NAME=aiac-flowpilot — isolates the RQ queue name.
- EMBEDDED_RQ_WORKER_ENABLED=true — supervises one RQ worker child process inside the existing API service.
- QSTASH_WORKFLOW_ENABLED=false and INLINE_EXECUTION=false — select RQ instead of the older QStash or in-process execution paths.
- WORKFLOW_JOB_TIMEOUT_SECONDS=600 — bounds each workflow job.
- FLOWPILOT_GATEWAY_CREDENTIAL_KEY — Fernet key used to encrypt short-lived Gateway session tokens stored in Redis with a TTL.

DATABASE_URL is optional. Leave it unset for this zero-new-database setup; when configured, PostgreSQL remains the preferred run-state backend while Redis is still used for the RQ queue.

The embedded worker is deliberately limited to **one concurrent workflow** on the Free API instance. It uses RQ's SimpleWorker in a supervised child process: long model calls do not occupy the FastAPI request loop, and Stop can terminate the isolated worker process without killing the API. The supervisor starts a replacement worker after a stop or unexpected worker exit. Run state and queued jobs remain in Upstash Redis across Render process restarts.

**Run controls:** Pause prevents a queued job from starting. For a running job, the model call is allowed to reach its next completion boundary and its result is held in Redis until Resume; Resume commits that result without repeating the inference. Stop marks the run cancelled, cancels queued work or terminates the isolated worker process, and prevents any late result from replacing the cancelled state. An external provider request already accepted by the provider may still finish remotely.

**Free-tier limits:** this shares the API service's CPU and memory, so it is suitable for the portfolio/demo workload rather than heavy parallel load. Render's free service may sleep when idle; queued work is consumed when the service is awake. The existing Upstash Free database is shared with the Portfolio LLM Gateway, so its 500K monthly-command, 256 MB data, and 10 GB monthly-bandwidth limits apply to both applications together. Keep the separate aiac-flowpilot key prefix and monitor combined usage.

### Alternative queue deployments

render.durable-worker.yaml and render.production.yaml describe optional paid topologies with separate worker/Redis/PostgreSQL resources. **Do not apply those manifests for the current no-cost deployment.** The current path does not provision or upgrade any paid service.

The previous QStash HTTP-message integration remains in the code as a compatibility path, but it is disabled in the current deployment configuration. The old QStash credentials can remain in Render without being used.

## Local development

Copy the example environment file, set local-only credentials, and start the Compose stack:

```bash
cp .env.example .env
docker compose up --build
```

The Compose stack includes the API, Streamlit UI, LiteLLM, PostgreSQL, Redis, and observability services. The exact services and health checks are defined by `docker-compose.yml`.

## Validation

Run the repository checks from its root:

```bash
ruff check app ui tests scripts
python -m compileall -q app ui tests scripts
pytest -q
```

For a Docker test run, build the test target first, then run the resulting image:

```bash
docker build --no-cache --target test -t flowpilot-test .
docker run --rm flowpilot-test
```

A passing CI run establishes the checks included in that workflow; it does not by itself verify live provider credentials, Render environment configuration, external integrations, or hosted end-to-end behavior.

## Safety and operating boundaries

- Content and outreach workflows prepare drafts; external publishing or sending is not automatic.
- API permissions are role-scoped.
- Gateway tokens are short-lived and project-scoped.
- Workflow execution and approval state are kept distinct.
- A demo deployment should use synthetic/sample inputs, not confidential client material.

## Portfolio positioning

**FlowPilot — AI Workflow Automation:** a distinct AI application for orchestrating bounded workflows, tracking execution, and keeping consequential actions reviewable. It is not the shared Evaluation Harness dashboard and is not the private Agency OS.
## Hosted deployment status

The existing public services are:

- UI: `https://ai-automation-ui-ac2c.onrender.com`
- API: `https://ai-automation-api.onrender.com`

The prior hosted revision was last health-checked on November 25, 2026. It reported `state_store=ephemeral-memory` and used the earlier QStash path. **That is not the target configuration described above.** The no-new-service RQ/Redis topology becomes active only after the updated API code is deployed and the existing API service receives its native TLS `REDIS_URL` and worker flags.

After rollout, verify `/`, `/health`, and `/ready`: the root endpoint should report `state_store=redis`, the worker execution mode should be `embedded_rq_worker`, and readiness should return `state_store=redis`. These checks do not invoke an LLM provider. A separate synthetic workflow/BYOK smoke test should be run only when provider usage is acceptable.
