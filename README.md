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

- **Frontend:** Streamlit operator workspace (`ui/app.py`).
- **API:** FastAPI control plane (`app/api/main.py`).
- **Execution:** the lightweight portfolio Render topology uses in-process background execution; the durable topology uses the existing Redis Queue (RQ) worker, with PostgreSQL-backed run state.
- **State:** persistent PostgreSQL/Redis support in the durable topology; the lightweight portfolio manifest documents ephemeral run state for the inline demo configuration.
- **LLM access:** portfolio BYOK uses a short-lived, project-scoped Gateway session; raw provider credentials are not forwarded to the application.
- **Observability:** health, run metrics, structured logs, and optional OpenTelemetry instrumentation.

## BYOK and Gateway handoff

When FlowPilot is launched from the portfolio website, the website owns provider selection and the user-supplied provider credential. FlowPilot receives a single-use handoff code, removes it from the URL, exchanges it server-side, and keeps the short-lived project token in Streamlit session state. The API validates the token for the FlowPilot project before using it for inference.

Do not put provider API keys or reusable Gateway bearer tokens in source control, URL parameters, or browser storage.

## Deployment

The root `render.yaml` defines a portfolio/demo topology with two Render web services:

- `ai-automation-api` — FastAPI API, using inline background execution and ephemeral run state.
- `ai-automation-ui` — Streamlit workspace, configured to call the API service.

This is intentionally a lightweight demo topology, not the durable PostgreSQL + Redis worker topology. See `render.production.yaml` for the separate durable deployment manifest and review its required secrets and resources before using it.

Required or externally supplied Render configuration includes the LLM provider credential, Gateway URL, allowed CORS origins, and the Gateway handoff root URL. Generated API role keys are provisioned by the manifest. Never commit real values.

### Background execution with plain QStash messages

The free Render deployment uses the existing QStash account as a single-job HTTP message queue. It does **not** use the Upstash Workflow SDK or its step-replay protocol. QStash publishes one message to the existing FlowPilot API, the API verifies the signed delivery, executes the job, and persists the run result. QStash's native failure callback records delivery failures and timeouts.

Configure these variables on **ai-automation-api** in Render (the existing names are retained for compatibility):

- `QSTASH_WORKFLOW_ENABLED=true` — legacy setting name; it now selects the plain QStash message queue.
- `QSTASH_TOKEN` — token for the selected QStash region.
- `QSTASH_CURRENT_SIGNING_KEY` and `QSTASH_NEXT_SIGNING_KEY` — signing keys for that same region.
- `QSTASH_URL` — e.g. `https://qstash-eu-central-1.upstash.io`.
- `UPSTASH_WORKFLOW_URL` — legacy setting name; supply the public FlowPilot API origin, e.g. `https://ai-automation-api.onrender.com`. It is used to construct the signed execute and failure-callback URLs.
- `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` — Fernet key used to encrypt short-lived BYOK session tokens before dispatch.

The raw BYOK bearer is never included in the QStash message. The envelope contains only encrypted credential ciphertext, a short TTL, owner scope, run ID and workflow input. Each run has a generation number and a recorded QStash message ID; duplicate and superseded deliveries are acknowledged without invoking the model again. Both execute and failure endpoints verify the QStash signature against the exact callback URL and request body.

**Run controls:** Run History exposes Pause, Resume and Stop. Pausing a queued run attempts to cancel its pending QStash message. If execution is already in progress, the model call may finish but its result is held until Resume. Resume either commits that held result without another model call or republishes queued work as a new generation. Stop marks the run canceled and attempts to cancel its pending message; a model request already in flight is not forcibly interrupted, but its late result is discarded and cannot revive the run.

**Execution limit:** The QStash free tier permits HTTP responses up to 15 minutes. FlowPilot uses a 14-minute message timeout to leave room for the native failure callback. Longer jobs are marked failed; they are not left indefinitely active. The free QStash quota and message/flow-control limits still apply.

**Persistence boundary:** The current free Render deployment still uses the in-memory FlowPilot run repository unless `DATABASE_URL` is configured. QStash can redeliver a message and the callback can rehydrate that individual run record, but a Render restart can still erase completed run history and other in-memory state. A fully durable queue and history require persistent PostgreSQL/Redis plus the dedicated RQ worker topology; that is a separate paid deployment and has not been provisioned.

The existing `flowpilot-global` flow-control key caps concurrent deliveries to protect the small free Render API instance. Use this as a portfolio/demo job queue, not an SLA-backed production queue.

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
## Live portfolio demo verification

The website-configured hosted endpoints were checked on November 25, 2026:

- UI: `https://ai-automation-ui-ac2c.onrender.com` — Streamlit `/_stcore/health` returned `ok`.
- API: `https://ai-automation-api.onrender.com/health` returned `status: ok`.
- API: `https://ai-automation-api.onrender.com/ready` returned `status: ready`.

The live API reported `execution_mode=inline_background` and `state_store=ephemeral-memory`. This is the lightweight portfolio/demo topology declared by the root `render.yaml`; it is not the durable PostgreSQL + Redis worker topology described in `render.production.yaml`. Run records from this hosted demo should therefore be treated as ephemeral. The durable topology requires its separately provisioned resources and configuration.

These checks confirm service health/readiness only. A full live workflow execution with a real BYOK session and approval path still needs a synthetic end-to-end test.
