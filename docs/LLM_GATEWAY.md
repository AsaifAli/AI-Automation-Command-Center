# LLM Gateway

This project uses a small LiteLLM proxy as the deployment-time LLM boundary. The application talks to an internal OpenAI-compatible endpoint instead of embedding an LLM model in the project container.

## Default routing

`portfolio-free` -> OpenRouter `openrouter/free` -> LiteLLM fallback -> Gemini Flash Lite.

OpenRouter's `openrouter/free` router dynamically selects an available free model. The exact upstream model can be pinned later with `OPENROUTER_LITELLM_MODEL` without changing application code.

## Environment

Create `.env` from `.env.example` and set:

- `LITELLM_MASTER_KEY` — an internal gateway key; generate a strong random value.
- `OPENROUTER_API_KEY` — your own OpenRouter key.
- `GEMINI_API_KEY` — optional fallback key.
- `OPENROUTER_LITELLM_MODEL` — defaults to `openrouter/free`.
- `GEMINI_LITELLM_MODEL` — defaults to `gemini/gemini-flash-lite-latest`.

Never commit `.env` or provider API keys. Only `.env.example` belongs in Git.

## Local run

```bash
docker compose up --build
```

If no upstream provider key is configured, the application may start but LLM requests will fail rather than silently using a bundled model. This keeps deployment behavior explicit and avoids shipping multi-GB model weights.

## Optional self-hosted inference

Projects that already support Ollama or vLLM retain those local/self-hosted paths. They are intentionally not required by the default public deployment.



## Secure queued BYOK handoff

FlowPilot UI exchanges the portfolio's single-use `flowpilot` handoff code on
the server and keeps the short-lived Gateway bearer in Streamlit server-side
session state. When a user queues a workflow, the API verifies the bearer
against the Gateway session-status endpoint and requires `project=flowpilot`
and `session_project=portfolio`. The API then receives the bearer only in
`X-LLM-Gateway-Token`.

For asynchronous runs, the API encrypts the bearer with the application Fernet
key `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` and stores the ciphertext in the
PostgreSQL `gateway_job_credentials` table with a short TTL. RQ/Redis receives
only a random credential UUID, never the bearer. The worker atomically
consumes the row, decrypts the bearer in memory, uses it for that workflow,
and clears the request context afterward. The reusable bearer is never
included in RQ arguments, Redis job payloads, workflow payload JSON, or logs.

Configure both URLs separately:

- `PORTFOLIO_SESSION_GATEWAY_URL=https://your-gateway-host` (UI handoff-code exchange)
- `LLM_GATEWAY_URL=https://your-gateway-host/v1` (API verification and worker inference)

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the resulting `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` identically for the API
and worker. Keep it stable while queued BYOK jobs may exist. Queued BYOK
requires PostgreSQL; it is deliberately rejected when only ephemeral in-memory
storage is configured. The default encrypted credential TTL is 900 seconds
and may be set between 60 and 1800 seconds, capped by the Gateway session's
remaining lifetime. If a job starts after expiry or after another worker has
consumed the reference, it fails closed and requires a fresh portfolio
handoff.

The service-level LiteLLM key remains the route for runs without a Gateway
session. A valid FlowPilot Gateway session takes precedence over demo mode.
The shared Portfolio LLM Gateway must allow the `flowpilot` project slug.
Gateway BYOK usage reports token counts but does not provide provider-specific
pricing, so FlowPilot marks the BYOK cost estimate unavailable rather than
applying its service-level LiteLLM price assumptions.
