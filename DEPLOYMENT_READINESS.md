# Deployment Readiness

Status: Render demo-ready.

## Public demo topology

Two Render Free web services:

- `ai-automation-api` — FastAPI API
- `ai-automation-ui` — Streamlit UI

The public demo intentionally does **not** provision a Render Postgres database. The API runs with `INLINE_EXECUTION=true` and uses an ephemeral in-memory state store when `DATABASE_URL` is unset. Run history, approvals, and audit state are therefore disposable and tied to the running process.

The full local Compose stack can still provide PostgreSQL when durable local testing is useful.

## Demo persistence boundary

This deployment is intentionally a portfolio/demo environment rather than a durable production system. A service restart, redeploy, or instance replacement can clear workflow history and approvals.

## First deployment test

1. API `/health`
2. API `/ready`
3. UI loads
4. Run a small demo workflow
5. Confirm run details and metrics are visible
6. Restart/redeploy and confirm that state is treated as disposable
