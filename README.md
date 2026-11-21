# FlowPilot — AI Automation Command Center

FlowPilot is a portfolio demonstration of an agentic operations workspace. It brings several business workflows into one place and makes orchestration, execution status, evidence, review and observability visible instead of hiding everything inside a single prompt.

It is a demonstration of the engineering pattern and the workflow experience. It is not presented as a managed, multi-tenant SaaS product or as a system already operating client processes.

## What the demo shows

- **Content Studio:** creates channel-aware drafts from complete briefs, with structured output and character limits.
- **Competitor Intelligence:** gathers bounded RSS/Atom signals and turns them into an evidence-oriented briefing.
- **Outreach Agent:** qualifies a candidate and prepares a draft; the workflow pauses for an authorized reviewer.
- **Approval Inbox:** reviewers see pending content-publishing and outreach proposals, inspect the exact drafts, and approve or reject with an auditable decision.
- **KPI & Progress:** normalizes updates, highlights blockers and prepares a leadership summary.

The UI presents run status, output, evidence, usage and review state. External publishing, sending or other external-impact actions are not silently performed by the demo.

## Engineering shape

```text
Streamlit Operations Workspace
            │
            ▼
      FastAPI Control Plane
            │
      ┌─────┴─────────┐
      │               │
   Inline demo     Redis / RQ
   (Render)        (local Compose)
      │               │
      └──────┬────────┘
             ▼
       LangGraph Workflows
       ├── Content
       ├── Competitor
       ├── Outreach
       └── KPI
             │
             ▼
 Validation · Evidence · Review
             │
             ▼
      Results and Run History
```

The hosted portfolio demo uses the existing two-service Render topology with inline execution and ephemeral state. Local Docker Compose is the fuller distributed reference, with PostgreSQL, Redis/RQ and observability services. The local topology is useful for demonstrating the architecture; it does not change the scope of the public portfolio demo.

## Bring your own provider key from the portfolio

FlowPilot follows the same shared Portfolio LLM Gateway pattern used by EvidenceFlow and QuoteSense:

1. The portfolio creates a short-lived, project-bound, single-use handoff code for `flowpilot`.
2. FlowPilot removes `portfolio_llm_handoff` from the browser URL and exchanges the code from the Streamlit server.
3. The resulting short-lived bearer is kept in Streamlit server-side session state. A reusable JWT is not accepted from a URL.
4. The app forwards the bearer to its API through `X-LLM-Gateway-Token`; the API verifies the active `flowpilot` session with the shared Gateway.
5. In queued local runs, the bearer is encrypted before storage and the queue receives only an opaque credential reference.

Configure the two Gateway addresses separately:

```dotenv
# Streamlit UI: Gateway origin used for the one-time code exchange.
PORTFOLIO_SESSION_GATEWAY_URL=https://<gateway-origin>

# API (and worker for queued local runs): OpenAI-compatible base URL.
LLM_GATEWAY_URL=https://<gateway-origin>/v1
```

The shared Gateway must include the `flowpilot` target in its handoff allowlist. The latest FlowPilot allowlist change is already part of the shared Gateway repository; deploy that Gateway revision before testing a new portfolio launch. Do not point `PORTFOLIO_SESSION_GATEWAY_URL` at the local LiteLLM proxy.

## Local setup

Use the existing checkout and existing `.env`; do not overwrite local credentials. For a first-time setup only:

```bash
python -m pip install -r requirements-dev.txt
python scripts/bootstrap_env.py
```

For live service-level inference, configure the relevant provider key(s) and LiteLLM settings in `.env`. For portfolio BYOK, set both Gateway URLs above. The environment bootstrap intentionally does not overwrite an existing `.env`.

Recommended local LLM timing defaults for complete structured outputs:

```dotenv
REQUEST_TIMEOUT_SECONDS=120
MAX_RETRIES=0
RUN_REQUEST_TIMEOUT_SECONDS=150
API_STARTUP_TIMEOUT_SECONDS=90
API_STARTUP_RETRY_INTERVAL_SECONDS=4
```

Start the stack and open the UI:

```bash
docker compose up --build -d
```

- UI: `http://localhost:8501`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Validation

```bash
python -m pytest -q
ruff check app ui tests scripts
python -m compileall -q app ui tests scripts
PYTHONPATH=. python scripts/evaluate.py
docker compose config --quiet
python scripts/smoke_test.py --base-url http://localhost:8000 --timeout 240
```

The smoke test exercises all four workflows. Content and Outreach must first appear in the reviewer inbox as `awaiting_approval`, then transition to `approved` after the test reviewer records a decision. Competitor and KPI must finish as `completed` or `completed_with_warnings`. A worker process merely exiting successfully is not enough.

## Render demo configuration

`render.yaml` remains the existing low-cost API + UI portfolio deployment:

- API runs inline, so a create-run request stays open while the workflow completes.
- The Streamlit UI retries API startup checks for up to 90 seconds, allowing a sleeping Free API service to wake without forcing the user to restart the UI.
- The inline workflow request can wait up to 150 seconds; ordinary API reads remain short.
- FlowPilot makes one Gateway request per inference attempt. The shared Gateway owns provider-level transient retries, avoiding nested retry chains in the public workflow request.
- Provider/model `404` responses and empty or malformed successful completions are reported as actionable failures and are not retried as transient network errors.
- The UI receives the operator API credential only; reviewer/admin credentials are not injected into the public UI.
- Run state on the free demo instance is ephemeral and can reset when the service restarts.

No paid durable deployment, multi-tenant onboarding system, customer-data isolation programme or client-production SLA is implied by this portfolio demonstration.

## Project links

- FlowPilot source: <https://github.com/AsaifAli/AI-Automation-Command-Center>
- Shared Portfolio LLM Gateway: <https://github.com/AsaifAli/shared-Portfolio-LLM-Gateway>
- EvidenceFlow: <https://github.com/AsaifAli/LangGraph-RAG>
- QuoteSense: <https://github.com/AsaifAli/quotation-analyzer>


## Human approval behavior

Content and outreach runs enter `awaiting_approval` after their drafts are prepared. Competitor and KPI workflows complete automatically. Approvals are role-checked, persisted with the audit history, and cannot be replayed. Approval authorizes the proposed action only: because this portfolio demo has no live publishing or sending connector, it never sends a message or publishes content.
