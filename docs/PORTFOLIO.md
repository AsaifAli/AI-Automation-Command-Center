# Portfolio & Interview Guide

## 30-second pitch

> I built an AI Automation Command Center that treats agents as production workflows rather than chatbots. It uses FastAPI and LangGraph for orchestration, Redis/RQ for asynchronous execution, PostgreSQL for durable state and auditability, and Prometheus/OpenTelemetry for observability. I also added human approval for external-impact outreach workflows and deterministic evaluation so the project can be tested without an LLM API key.

## 90-second demo

1. Start the stack with `docker compose up --build -d`.
2. Open the Streamlit operations console.
3. Run Content and show channel-aware drafts.
4. Run Competitor Intelligence and show evidence-oriented output.
5. Run Reachout and explain the approval boundary.
6. Approve the run and show the audit trail.
7. Run KPI and demonstrate blocker detection.
8. Open Swagger to show the API contract.
9. Open Grafana to show workflow operational metrics.
10. Open Jaeger to show distributed workflow traces.
11. Run the deterministic evaluation suite through the container.

## Strong interview talking points

### Why not execute directly inside FastAPI?

LLM calls and external intelligence sources can be slow or fail. Queue-based execution separates user-facing latency from agent execution and gives a path to multiple workers.

### Why PostgreSQL?

Workflow state, approvals and audit records are business data. Durable relational storage is a better fit than an embedded database once multiple services are involved.

### Why Redis/RQ?

It is a deliberately lightweight job queue for this portfolio project. It demonstrates asynchronous architecture without introducing a large distributed streaming stack.

### Why LangGraph?

The project has explicit workflow stages and guardrails. LangGraph makes state transitions visible and extensible compared with a single LLM call.

### Why human approval for outreach?

Sending external communication is a side effect. The system should not let an LLM independently cross a business authorization boundary.

### How would you scale it?

Keep the API stateless, run multiple workers, move PostgreSQL/Redis to managed services, and add queue concurrency/rate policies.

### How would you evaluate it?

Use workflow-specific datasets and measure factuality, source precision, approval rate, latency, cost, failure rate and downstream business outcomes. The current repository includes deterministic regression gates as a foundation.

## Resume bullets

- Engineered a Dockerized AI automation platform using Python, FastAPI and LangGraph to orchestrate content, competitor intelligence, partner outreach and KPI workflows.
- Implemented asynchronous Redis/RQ workers with PostgreSQL-backed workflow state, scheduled jobs, approval controls and auditable execution history.
- Built deterministic AI evaluation gates and production observability with Prometheus, Grafana, OpenTelemetry and Jaeger to measure workflow reliability, cost and operational health.

## Portfolio headline

**AI Automation Command Center — asynchronous agent orchestration with human oversight, evidence, evaluation and observability.**
