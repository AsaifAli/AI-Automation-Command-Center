# ⚡ AI Automation Command Center

> **Portfolio-grade AI automation platform with LangGraph orchestration, async workers, human approval, evidence, PostgreSQL persistence, Redis queues, scheduled workflows, Prometheus/Grafana metrics, OpenTelemetry traces and Docker Compose deployment.**

This project is a from-scratch implementation of a production-minded AI automation control plane. It demonstrates how AI agents can be turned into **reliable business workflows** rather than isolated LLM demos.

The domain model covers four automation patterns: content generation, competitor intelligence, partner outreach, and KPI/leadership reporting.

## 🚀 Deployment

**Status:** Deployed

The application is deployed as a public portfolio demonstration.

**Architecture:** GitHub Actions → Docker → Cloud deployment

> Live demo access is provided selectively for evaluation/interviews.

## Why this project stands out

Most agent portfolios stop at `prompt -> LLM -> answer`. This project demonstrates the engineering layer around the model:

- **LangGraph** workflow orchestration
- **FastAPI** control plane with typed contracts
- **Redis + RQ** asynchronous job execution
- **PostgreSQL** durable run and audit storage
- **Human-in-the-loop** approval for external-impact workflows
- **Evidence and validation** instead of unsupported claims
- **Scheduled automation** through a dedicated scheduler service
- **Prometheus + Grafana** operational metrics
- **OpenTelemetry + Jaeger** distributed tracing
- **Deterministic demo mode** so the application runs without API credentials
- **CI evaluation suite** for workflow regression
- **Docker Compose-only deployment** — no local Python/Node/Postgres/Redis installation required

## Architecture

```text
                         ┌───────────────────────┐
                         │      Streamlit UI      │
                         │   Operations Console   │
                         └───────────┬───────────┘
                                     │ HTTP
                                     ▼
                         ┌───────────────────────┐
                         │       FastAPI API      │
                         │ auth · validation ·    │
                         │ request IDs · contracts│
                         └───────┬─────────┬──────┘
                                 │         │
                          enqueue│         │read/write
                                 ▼         ▼
                         ┌────────────┐  ┌─────────────┐
                         │   Redis    │  │ PostgreSQL  │
                         │ job queue  │  │ runs/audit  │
                         └─────┬──────┘  └─────────────┘
                               │
                               ▼
                     ┌──────────────────────┐
                     │    RQ Worker(s)       │
                     │                      │
                     │ LangGraph             │
                     │   ├─ Content Agent    │
                     │   ├─ Competitor Agent │
                     │   ├─ Outreach Agent   │
                     │   └─ KPI Agent        │
                     └──────────┬───────────┘
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
          ┌──────────────┐             ┌──────────────┐
          │ LLM Provider │             │ Intelligence │
          │ demo / API   │             │ RSS/Atom     │
          └──────────────┘             └──────────────┘

Observability:
  API/Worker → OpenTelemetry → Collector → Jaeger
  API → Prometheus → Grafana

Automation:
  Scheduler → Redis → Worker → PostgreSQL
```

## Services

| Service | Purpose | Default port |
|---|---|---:|
| `api` | FastAPI control plane | 8000 |
| `ui` | Streamlit operations console | 8501 |
| `worker` | Async LangGraph execution | internal |
| `scheduler` | Recurring automation trigger | internal |
| `postgres` | Durable state and audit trail | internal |
| `redis` | Queue and job broker | internal |
| `prometheus` | Metrics collection | 9090 |
| `grafana` | Operations dashboard | 3000 |
| `jaeger` | Distributed trace UI | 16686 |
| `otel-collector` | Trace pipeline | internal |

## 🚀 Deploy with Docker Compose only

### 1. Requirements

You only need:

- Docker Desktop, or
- Docker Engine + Docker Compose v2

You do **not** need to install Python, PostgreSQL, Redis, Streamlit or Grafana locally.

### 2. Start

```bash
docker compose up --build -d

# Run the full Docker Compose smoke test after the stack is healthy
python scripts/smoke_test.py
```

The project defaults to deterministic demo mode, so an LLM API key is not required. The scheduler also queues a demo competitor-intelligence run immediately on startup and then follows the configured interval.

### 3. Check services

```bash
docker compose ps
```

The API readiness endpoint verifies both PostgreSQL and Redis:

```text
http://localhost:8000/ready
```

### 4. Open the application

- **UI:** http://localhost:8501
- **API:** http://localhost:8000
- **Swagger:** http://localhost:8000/docs
- **Prometheus:** http://localhost:9090
- **Grafana:** http://localhost:3000
- **Jaeger:** http://localhost:16686

Default Grafana credentials are `admin` / `change-me` unless overridden in `.env`.

### 5. Scale workers

Because execution is queue-based, you can scale workers without changing the API:

```bash
docker compose up --build -d

# Run the full Docker Compose smoke test after the stack is healthy
python scripts/smoke_test.py --scale worker=2
```

### 6. Stop

```bash
docker compose down
```

### 7. Remove all persistent demo data

```bash
docker compose down -v --remove-orphans
```

## Configuration

Create a `.env` file if you want to override defaults:

```bash
cp .env.example .env
```

Important settings:

```env
DEMO_MODE=true

# Optional real LLM
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Optional API protection
API_AUTH_ENABLED=false
API_KEY=

# Scheduler
SCHEDULE_ENABLED=true
SCHEDULE_INTERVAL_MINUTES=60
SCHEDULE_WORKFLOW=competitor

# Observability
OTEL_ENABLED=true
```

### Production secrets

Never commit `.env`. Use a secret manager in a real deployment. At minimum, change:

- `POSTGRES_PASSWORD`
- `GRAFANA_PASSWORD`
- `API_KEY` if API authentication is enabled
- LLM credentials

## Workflow model

Every submitted run follows:

```text
API request
    ↓
Pydantic validation
    ↓
PostgreSQL: queued
    ↓
Redis queue
    ↓
RQ worker
    ↓
LangGraph
    ├── execute
    └── validate
    ↓
Evidence + usage + warnings
    ↓
PostgreSQL: completed / failed
    ↓
UI polling / audit history
```

### Content Agent

Creates channel-aware drafts for LinkedIn, X and Telegram. Publishing remains a human decision.

### Competitor Intelligence Agent

Collects optional RSS/Atom signals, associates observations with configured entities, and explicitly separates evidence from inference.

### Outreach Agent

Qualifies candidates and produces a draft. **It cannot send an external message.** A reviewer must approve the candidate through the approval API/UI.

### KPI Agent

Normalizes updates, detects blockers, and produces a leadership-oriented summary.

## Human-in-the-loop safety

External-impact actions are intentionally controlled.

For outreach:

```text
Candidate
   ↓
Qualification
   ↓
Draft
   ↓
approval_required = true
   ↓
Human reviewer
   ├── approved
   └── rejected
```

The decision is stored in PostgreSQL and added to the audit trail.

This is an important design choice: **autonomy is useful only when the risk boundary is explicit.**

## Observability

### Metrics

FastAPI exposes:

```text
GET /metrics
GET /prometheus
```

Prometheus scrapes `/prometheus` and Grafana is pre-provisioned with a dashboard containing:

- total workflow runs
- completed runs
- failed runs
- queued/running state
- approval totals
- approved outreach

### Tracing

API and worker workflow spans are exported through OpenTelemetry Collector to Jaeger.

Use Jaeger to inspect workflow execution traces at:

```text
http://localhost:16686
```

Observability is deliberately optional at the application layer; if telemetry configuration fails, the core automation system continues to run.

## API examples

### Queue a run

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "workflow": "content",
    "payload": {
      "topics": ["AI agents", "automation"],
      "channels": ["linkedin", "x"],
      "tone": "executive"
    }
  }'
```

The API returns `202 Accepted` with a run ID.

### Poll the run

```bash
curl http://localhost:8000/api/v1/runs/<RUN_ID>
```

### Approve outreach

```bash
curl -X POST http://localhost:8000/api/v1/runs/<RUN_ID>/approval \
  -H 'Content-Type: application/json' \
  -d '{
    "decision": "approved",
    "reviewer": "Portfolio Reviewer",
    "note": "Reviewed for demo"
  }'
```

## Evaluation

The evaluation suite is deterministic and can run without an external LLM.

```bash
docker compose run --rm api python scripts/evaluate.py
```

Current gates cover:

- content output structure
- competitor action generation
- outreach approval enforcement
- KPI blocker detection

See [`docs/EVALUATION.md`](docs/EVALUATION.md) for production evaluation dimensions and regression strategy.

## Testing and quality checks

The deployment path remains Compose-only. For CI, the same Docker image can run project checks:

```bash
docker compose run --rm api pytest -q
docker compose run --rm api ruff check .
docker compose run --rm api python -m compileall -q app ui tests scripts
```

GitHub Actions also performs dependency installation, linting, tests, evaluation and Docker image build.

## Project structure

```text
app/
├── agents/             Business-level AI workflows
├── api/                FastAPI control plane
├── core/               Configuration, logging, telemetry
├── integrations/       LLM and intelligence adapters
├── models/              API/domain schemas
├── storage/             PostgreSQL repository
├── workflows/           LangGraph orchestration
├── workers/             Redis/RQ execution jobs
├── worker.py            Worker entrypoint
└── scheduler.py        Scheduled workflow entrypoint

ui/                     Streamlit operations console
evals/                  Deterministic quality cases
scripts/                Evaluation + health utilities
tests/                  Unit/API tests
docs/                   Architecture, evaluation, security, portfolio guides
observability/          Prometheus, Grafana and OTel configuration
```

## Engineering trade-offs

### Why Redis + RQ?

The project needs a clear separation between HTTP request handling and long-running agent execution. Redis/RQ is intentionally simpler than introducing a full event-streaming platform while still demonstrating queue-based execution.

### Why PostgreSQL?

Runs, approvals and audit events are durable business data. PostgreSQL is a better portfolio signal than an embedded SQLite database and provides a natural migration path to managed production infrastructure.

### Why Docker Compose?

The goal is a reproducible multi-service deployment that a recruiter can launch with one command. Compose also makes the architecture visible without requiring Kubernetes.

### Why deterministic demo mode?

A portfolio project should remain runnable after cloning. External API credentials should enhance the demo, not be a prerequisite for seeing the architecture.

### Why not fully autonomous outreach?

External communication creates a side-effect boundary. The project demonstrates controlled autonomy and human approval rather than treating an LLM as an unrestricted actor.

## Production roadmap

The project is intentionally deployable now, while leaving room for realistic evolution:

- OAuth/OIDC + RBAC
- managed PostgreSQL/Redis
- secret manager integration
- richer Web3 connectors such as DeFiLlama/Dune/Apify
- vector memory/RAG where justified by the workflow
- model routing and policy-based model selection
- workflow concurrency controls
- dead-letter queues and retry policies
- artifact/object storage
- automated ROI measurement
- stronger LLM-as-judge + human evaluation loops
- Kubernetes deployment for teams that actually need it

## Portfolio positioning

**One-line pitch:**

> Built a production-minded AI automation command center that turns agent capabilities into observable, asynchronous and approval-controlled business workflows.

**Resume-ready bullets:**

- Engineered a Dockerized AI automation platform using Python, FastAPI and LangGraph to orchestrate content, competitor intelligence, partner outreach and KPI workflows.
- Implemented asynchronous Redis/RQ execution with PostgreSQL-backed run state, audit events, scheduled jobs and failure-aware workflow validation.
- Added human-in-the-loop controls, evidence capture, deterministic evaluation, Prometheus/Grafana metrics and OpenTelemetry tracing to make agent workflows observable and production-oriented.

## License

MIT — see [`LICENSE`](LICENSE).


## Render portfolio deployment

The public demo uses the included `render.yaml` Blueprint:

- `ai-automation-api`: FastAPI web service
- `ai-automation-ui`: Streamlit web service
- `ai-automation-db`: Render Postgres

For the public demo, Redis, the background worker, scheduler, LiteLLM gateway, and local observability stack are intentionally kept out of the cloud path. The API runs workflows inline when `INLINE_EXECUTION=true`, while the LLM is accessed through an external OpenAI-compatible HTTPS endpoint.

This keeps the deployed demo small while preserving the complete distributed Compose architecture for local development and portfolio inspection.

**Free-tier caveat:** Render currently offers a Free Postgres instance, but Free Postgres expires after 30 days. This deployment is therefore a portfolio/demo deployment, not a durable production datastore. citeturn333765search0
