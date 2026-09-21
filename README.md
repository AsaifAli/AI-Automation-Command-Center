# ⚡ AI Automation Command Center

> **Portfolio-grade AI automation control plane with LangGraph orchestration, asynchronous workers, human approval, evidence, audit history, observability, and a polished operations workspace.**

AI Automation Command Center demonstrates how agentic systems become **reliable business workflows rather than isolated LLM demos**. The platform covers content generation, competitor intelligence, partner outreach, and KPI/leadership reporting.

## What changed in the portfolio-ready release

The project now includes a substantially more product-oriented operations UI while preserving the workflow backend:

- **Control-plane UX:** System Pulse, System Health, workflow preview, approvals, audit trail, and recent execution views.
- **Live workflow visualization:** active runs expose high-level lifecycle states without fabricating per-node telemetry.
- **One-click demo workflow:** demo runs use the real `/api/v1/runs` path rather than simulated UI output.
- **Execution details:** run metadata, status, duration, output, and audit information are surfaced in the UI.
- **Clear History:** history can be cleared through a protected API path while active jobs are preserved.
- **Reliable sidebar:** native Streamlit sidebar collapse/reopen behavior is repaired for hosted deployments.
- **Responsive theme-aware styling:** the UI is designed as an operational control plane rather than a generic chatbot.

## Why this project stands out

Most agent portfolios stop at `prompt -> LLM -> answer`. This project demonstrates the engineering layer around the model:

- **LangGraph** workflow orchestration
- **FastAPI** control plane with typed contracts
- **Redis + RQ** asynchronous job execution
- **PostgreSQL (optional local mode)** for durable run and audit storage
- **Human-in-the-loop** approval for external-impact workflows
- **Evidence and validation** instead of unsupported claims
- **Scheduled automation** through a dedicated scheduler service
- **Prometheus + Grafana** operational metrics
- **OpenTelemetry + Jaeger** distributed tracing
- **Deterministic demo mode** for portfolio demonstrations
- **CI evaluation suite** for workflow regression
- **Docker Compose** deployment

## Architecture

```text
Streamlit Operations Console
        ↓
FastAPI Control Plane
        ↓
Redis Queue (local async mode)
        ↓                  │
RQ Workers               Audit / History
        ↓
LangGraph Workflows
        ├── Content Agent
        ├── Competitor Agent
        ├── Outreach Agent
        └── KPI Agent
        ↓
Validation + Evidence + Usage
        ↓
Completed / Failed / Awaiting Approval

Observability:
Prometheus → Grafana
OpenTelemetry → Jaeger
```

## Human-in-the-loop safety

External-impact actions remain explicitly controlled. For outreach:

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

The approval decision is persisted in PostgreSQL and exposed in the audit trail. The UI intentionally treats autonomy and risk boundaries as separate concerns.

## Local development

```bash
docker compose up --build -d
python scripts/smoke_test.py
```

UI: `http://localhost:8501`  
API: `http://localhost:8000`  
Swagger: `http://localhost:8000/docs`

## Testing

```bash
docker compose run --rm api pytest -q
docker compose run --rm api ruff check .
docker compose run --rm api python -m compileall -q app ui tests scripts
```

## Portfolio positioning

**AI operations control plane** — agent orchestration, asynchronous execution, approval boundaries, observability, and auditability.
