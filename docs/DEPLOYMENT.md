# Docker Compose Deployment Guide

## Prerequisites

Install only Docker Desktop or Docker Engine with Docker Compose v2.

No Python, Node.js, PostgreSQL, Redis or monitoring stack is required on the host.

## First deployment

Install project dependencies in a Python 3.12 virtual environment, then create private local secrets once:

```bash
python -m pip install -r requirements-dev.txt
python scripts/bootstrap_env.py
```

The bootstrap refuses to overwrite an existing `.env` or Prometheus token. If you already have those files, keep them and manually add only missing settings from `.env.example`.

```bash
docker compose up --build -d
```

Verify:

```bash
docker compose ps
curl http://localhost:8000/ready
```

Expected readiness response includes:

```json
{"status":"ready","database":"available","redis":"available"}
```

## Service endpoints

| Component | URL |
|---|---|
| Streamlit | http://localhost:8501 |
| FastAPI | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Jaeger | http://localhost:16686 |

## Configuration

Copy `.env.example` to `.env` when overrides are needed.

The Compose file explicitly passes supported settings to the application containers, so the runtime does not depend on host-installed language tooling. Production images install only `requirements.txt`; CI and local development install `requirements-dev.txt` for pytest and Ruff.

## Demo vs LLM mode

### Demo

```env
DEMO_MODE=true
```

This requires no LLM credentials.

### LLM-backed

```env
DEMO_MODE=false
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
```

The LLM integration is isolated behind `LLMProvider` and supports OpenAI-compatible endpoints.

## Scheduled automation

Scheduled automation is disabled by default. Enable it only after reviewing the workflow, target entities and cadence.

```env
SCHEDULE_ENABLED=true
SCHEDULE_INTERVAL_MINUTES=60
SCHEDULE_WORKFLOW=competitor
SCHEDULE_COMPETITORS=Example Protocol,Example AI Startup
```

A run is queued immediately at scheduler startup, then repeated according to the interval.

## Scaling

The API is stateless and workers consume the shared Redis queue.

```bash
docker compose up --build -d --scale worker=3
```

Do not run multiple scheduler replicas unless you deliberately add leader election; one scheduler instance should own recurring triggers.

## Logs

All application logs can be viewed through Compose:

```bash
docker compose logs -f --tail=200
```

Specific service:

```bash
docker compose logs -f worker
```

## Restarting a service

```bash
docker compose restart worker
```

## Stop / reset safely

Stop containers without deleting persistent data:

```bash
docker compose down --remove-orphans
```

Only when you intentionally want to permanently erase PostgreSQL, Redis, Prometheus and Grafana data, run:

```bash
docker compose down -v --remove-orphans
```

This destructive command is also available as `make clean-volumes`; the default `make clean` preserves volumes.

## Production hardening checklist

Before production use:

- change all default passwords
- enable real authentication/RBAC
- use managed PostgreSQL
- use managed Redis
- move secrets to a secret manager
- put UI/API behind TLS
- restrict CORS
- restrict network access to observability services
- configure PostgreSQL backups
- configure Redis persistence/HA according to workload
- set resource limits
- set queue concurrency policies
- configure alerting
- add provider spend limits
- add data retention policies

## Troubleshooting

### API is unhealthy

```bash
docker compose logs api
curl http://localhost:8000/ready
```

Readiness checks PostgreSQL and Redis, so a database or broker problem will be visible.

### Runs remain queued

Check:

```bash
docker compose logs worker
```

and:

```bash
docker compose logs redis
```

The worker must be running and connected to the same Redis queue.

### UI cannot reach API

The UI uses the internal Compose hostname:

```text
http://api:8000
```

Do not change it to `localhost` inside the container.

### Grafana has no data

Confirm the API metrics endpoint:

```bash
curl http://localhost:8000/prometheus
```

Then check Prometheus targets at:

```text
http://localhost:9090/targets
```

### No traces in Jaeger

Check:

```bash
docker compose logs otel-collector
docker compose logs jaeger
```

Telemetry is intentionally non-blocking; the application can continue if the collector is unavailable.


## Render demo deployment

The Render demo uses `render.yaml` to create two web services:
- FastAPI API
- Streamlit UI

The cloud API runs with `INLINE_EXECUTION=true`; the create-run endpoint stores
an in-memory run reference, returns immediately, and continues model execution
in a FastAPI in-process background task. This avoids holding the UI's HTTP
request open for the full model latency. This blueprint does not provision
PostgreSQL, Redis, or an RQ worker. The local Compose topology remains the full
distributed architecture. Render run history therefore uses ephemeral
in-memory state and is intended for portfolio demonstrations, not durable
production operations. A service restart or deploy can interrupt an active
background run and clears its in-memory history.

Secrets and Gateway settings:
- `OPENAI_API_KEY` is supplied in the Render dashboard for the service-level fallback.
- `API_OPERATOR_KEYS` is generated by Render and injected into the UI as an operator-only credential. Reviewer/admin keys are not exposed to the public UI.
- Set `LLM_GATEWAY_URL` on the API service to `https://<gateway-origin>/v1`.
- Set `PORTFOLIO_SESSION_GATEWAY_URL` on the UI service to `https://<gateway-origin>` (no `/v1`).
- Set `CORS_ORIGINS` on the API service to the exact HTTPS Streamlit UI origin; do not use `*`.

Because the Render demo uses an in-process background task, a verified Gateway
bearer is passed directly to that task in process memory; it is not placed in a
queue or persisted. No queued-credential encryption key is needed for this
blueprint. For durable queued BYOK, use the PostgreSQL + Redis/RQ topology,
set the same `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` in API and worker, and keep
`INLINE_EXECUTION=false`.

## Durable Render topology

`render.production.yaml` is an alternate paid topology for queued operations. It provisions a paid API, UI, worker, scheduler, PostgreSQL and persistent Key Value service. It is not automatically selected by the existing Render Blueprint. Before switching an existing Render Blueprint to this file, review pricing, service names, data migration, database backups and resource ownership. Render warns against managing the same resource from multiple Blueprints.

Do not enable scheduled runs until the configured schedule workflow and targets are intentional. The production Blueprint defaults scheduling off.
