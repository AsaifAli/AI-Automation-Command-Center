# Deployment Readiness

Status: Render demo-ready.

## Public demo topology

Two Render Free web services plus one Render Free Postgres database:

- `ai-automation-api` — FastAPI API
- `ai-automation-ui` — Streamlit UI
- `ai-automation-db` — managed Postgres

The full local Compose stack remains available for development and includes Redis, worker, scheduler, LiteLLM and observability.

For the public demo, Redis/worker infrastructure is intentionally omitted. `INLINE_EXECUTION=true` makes the API execute a workflow in-process so the demo does not require a background worker or Redis. The LLM still uses an external OpenAI-compatible endpoint (OpenRouter).

Render Free Postgres is temporary: Render currently expires free Postgres databases after 30 days. This is acceptable for the portfolio demo, not for production. citeturn333765search0
