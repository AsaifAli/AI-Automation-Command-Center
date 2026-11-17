# Architecture & Engineering Decisions

## System goal

Build an AI automation control plane that demonstrates the engineering required to operate agents as business workflows: asynchronous execution, durable state, validation, human approval, scheduling, evidence, metrics and traces.

## Runtime topology

```text
Streamlit
   │
   ▼
FastAPI ───────► PostgreSQL
   │                  ▲
   │                  │
   ▼                  │
 Redis ───────► RQ Worker
                   │
                   ▼
                LangGraph
              execute → validate
                   │
          ┌────────┴────────┐
          ▼                 ▼
     LLM Provider      Intelligence

Scheduler ─────────► Redis

API/Worker ─► OTel Collector ─► Jaeger
API ───────────────► Prometheus ─► Grafana
```

## Request lifecycle

1. Client sends a validated request to FastAPI.
2. FastAPI creates a durable `queued` run in PostgreSQL.
3. The run is enqueued in Redis/RQ.
4. Worker marks the run `running`.
5. LangGraph executes the selected agent.
6. A validation node checks workflow-specific guardrails.
7. Evidence, usage, timing and warnings are attached.
8. PostgreSQL stores the final state and audit event.
9. UI polls the run and renders the result.

## Service responsibilities

### API

Owns HTTP contracts, request validation, authentication, enqueueing and read APIs. It does not execute long-running agent work.

### Worker

Owns agent execution. This keeps LLM/network latency out of the HTTP process and makes horizontal worker scaling possible.

### Scheduler

Creates recurring runs without embedding scheduling logic inside the API process.

### PostgreSQL

Stores workflow state, evidence, usage, warnings, approvals and audit events.

### Redis

Provides transient queue state and job dispatch.

## Agent boundary

Agents contain business logic. They do not know about HTTP, authentication or persistence.

## LLM boundary

`LLMProvider` isolates provider-specific HTTP details and exposes one interface to agents. Demo mode makes execution deterministic and credential-free.

## Intelligence boundary

`IntelligenceSource` currently supports RSS/Atom. The boundary is designed for future adapters such as Apify, Dune or DeFiLlama without rewriting the competitor workflow.

## Human-in-the-loop policy

Content and outreach agents finish their preparation work, then set the run to `awaiting_approval`. The run is visible in the reviewer-only Approval Inbox with the proposed action, draft content, workflow reference and evidence/output context.

- Content publishing and outreach sending require an authenticated reviewer or admin decision.
- Competitor intelligence and KPI reporting remain automatic because they prepare internal analysis and do not perform an external side effect.
- The decision is recorded atomically with the run status and audit event. A second or stale decision returns a conflict.
- `approved` means the action was authorized, not executed. This demo has no publishing/sending connector; the API records `execution_status=not_configured` and does not claim an external action occurred.
- Pending approval runs are protected from Clear History.

## Reliability

- async queue-based execution
- bounded provider retries
- provider timeout
- deterministic demo mode
- workflow validation
- durable status transitions
- structured failure state
- API health/readiness checks
- service healthchecks in Compose
- restart policies

## Observability

Prometheus provides operational counters through `/prometheus`. OpenTelemetry provides workflow traces. Both are included in Compose so the complete demo can be observed without external infrastructure.

## Scaling path

The architecture allows:

```text
1 API
   +
N workers
   +
1 scheduler
   +
managed Postgres
   +
managed Redis
```

Worker count can be increased without changing the API contract.

## Security boundaries

- secrets injected through environment variables
- optional API-key authentication
- Pydantic input validation
- CORS allowlist
- non-root application container
- no credentials baked into image
- human approval for external side effects
- production secret-manager migration documented
