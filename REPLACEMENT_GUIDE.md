# FlowPilot finish bundle — replacement and integration guide

This bundle finishes the Content Agent's live generation path and the secure
queued Portfolio LLM Gateway (BYOK) handoff. It contains complete replacement
files under their original paths. It does not include `.env`, secrets, `.git`,
`.venv`, Docker volumes, or local databases.

## 1. Back up and replace FlowPilot files

From the root of `ai-automation-command-center-render-ready`:

```bash
mkdir -p ../ai-automation-command-center-render-ready-backup
cp -R app ui tests docs Dockerfile docker-compose.yml requirements.txt \
  .env.example render.yaml README.md pyproject.toml \
  ../ai-automation-command-center-render-ready-backup/
unzip -o ~/Downloads/FlowPilot-Finish-v1.zip -d .
```

The FlowPilot replacement paths (`app/`, `ui/`, `tests/`, `docs/`, and the
root configuration files) are at the ZIP root and extract directly into this
repository. The `shared-Portfolio-LLM-Gateway/` directory is a companion for
your separate Gateway checkout; it will be extracted as a helper folder inside
this project. Copy its script and test file to the separate Gateway repository
as described in section 3; do not run the script from the FlowPilot root.

## 2. Configure local service-level LLM and queued BYOK

Keep the service-level LiteLLM route for workflows that do not carry a user
Gateway session:

```dotenv
DEMO_MODE=false
INLINE_EXECUTION=false
LLM_PROVIDER=litellm
OPENAI_BASE_URL=http://litellm:4000/v1
OPENAI_MODEL=portfolio-free
OPENROUTER_LITELLM_MODEL=openrouter/openrouter/free
```

Keep your existing `LITELLM_MASTER_KEY`, `OPENROUTER_API_KEY`, and optional
`GEMINI_API_KEY` in the private `.env`. Do not put credentials in this bundle
or commit `.env`.

For Portfolio Gateway BYOK, configure these three additional values in the
same private `.env`:

```dotenv
# UI exchanges a single-use code at this Gateway origin (no /v1 suffix).
PORTFOLIO_SESSION_GATEWAY_URL=https://<your-gateway-host>

# API validates the session and worker sends inference here (/v1 included).
LLM_GATEWAY_URL=https://<your-gateway-host>/v1

# Generate a NEW FlowPilot application key with the command below.
FLOWPILOT_GATEWAY_CREDENTIAL_KEY=<generated-fernet-key>
GATEWAY_JOB_CREDENTIAL_TTL_SECONDS=900
```

Generate the FlowPilot application key locally:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the generated value into your private `.env`. Docker Compose passes the
same `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` to the API and worker. Keep it stable
across restarts while queued BYOK jobs may exist. This key is different from
the shared Gateway's own `GATEWAY_ENCRYPTION_KEY`, the Gateway JWT signing key,
and `LITELLM_MASTER_KEY`.

The application supports `DEMO_MODE=true` with an active Gateway session too:
a valid BYOK session takes precedence over demo mode. With `DEMO_MODE=false`,
requests without a BYOK session use the service-level LiteLLM credentials.

## 3. Enable FlowPilot in the separate shared Portfolio LLM Gateway

The shared Gateway currently has its own project allowlist. It must explicitly
allow `flowpilot` before one-time handoff exchange can work.

From the root of your separate `shared-Portfolio-LLM-Gateway` checkout, run
the bundled allowlist installer:

```bash
python /path/to/FlowPilot-Finish-v1/shared-Portfolio-LLM-Gateway/apply_flowpilot_allowlist.py
```

It checks for the exact expected allowlist declaration, creates a backup of
`app/config.py`, and changes only that declaration. It aborts without changing
files if the source has drifted or if the expected declaration is ambiguous.

Replace the shared Gateway's test file with the complete file in this bundle:

```bash
cp /path/to/FlowPilot-Finish-v1/shared-Portfolio-LLM-Gateway/tests/test_session_handoff.py \
  tests/test_session_handoff.py
pytest -q tests/test_session_handoff.py
```

Commit, push, and deploy the shared Gateway update separately. Do this before
trying a FlowPilot BYOK handoff. The FlowPilot UI validates the exchange
response's `target_project`, and the FlowPilot API independently calls the
Gateway session-status endpoint to require an active `flowpilot` session
originating from the `portfolio` session.

## 4. Run local checks

With your existing Python 3.12 virtual environment activated:

```bash
ruff check app ui tests scripts
python -m compileall -q app ui tests scripts
python -m pytest -q
python scripts/evaluate.py
docker compose config --quiet
```

Do not run unsafe Ruff autofixes. The new tests cover:

- the Compose environment-variable alias for the Fernet key;
- encrypted credential storage and one-time consumption;
- the fact that only a credential UUID, never a bearer, enters RQ arguments;
- fail-closed behavior without PostgreSQL or the application encryption key;
- worker context cleanup after a queued job;
- active Gateway session precedence over demo mode;
- strict `flowpilot` project-audience validation;
- complete model-generated posts and human-review gates;
- comma-preserving, one-brief-per-line UI input parsing.

## 5. Rebuild the local Docker stack

Build every Python service image so all services use the updated dependencies
and source files. Then recreate only the application services:

```bash
docker compose build api worker scheduler ui
docker compose up -d --force-recreate api worker scheduler ui
docker compose ps
```

Do not use `docker compose down -v`; that would delete PostgreSQL, Redis,
Prometheus, and Grafana volumes.

Run the existing end-to-end smoke test:

```bash
python scripts/smoke_test.py --base-url http://localhost:8000 --timeout 120
```

## 6. Verify real Content Agent generation

Open `http://localhost:8501`, choose **Content Agent**, and enter one complete
brief per line. A single line may contain commas and full sentences; commas are
no longer treated as topic separators.

For example:

> Design a practical LinkedIn post about deploying multi-agent AI systems in enterprise finance. Explain invoice reconciliation, exception handling, and audit preparation, then discuss implementation risks and human oversight.

With the default channels selected, the agent should request one complete post
for each channel from the live model. The UI shows provider, model, and token
usage. All returned posts remain `needs_review` with
`human_approval_required`; the agent never publishes them. If the model returns
invalid or incomplete structured output, the workflow fails rather than
silently fabricating or clipping posts.

For service-level inference, the provider should be `litellm`. For a BYOK
handoff, the provider should be `portfolio-gateway` and the UI mode should show
`BYOK Gateway`. The shared Gateway currently returns token usage but no
provider-specific pricing, so BYOK cost is shown as unavailable rather than
misusing FlowPilot's LiteLLM price assumptions.

## 7. How queued BYOK is protected

1. The Streamlit UI exchanges the one-time portfolio code server-side and
   stores the short-lived bearer in server-side Streamlit session state.
2. The UI sends that bearer only in the API request header
   `X-LLM-Gateway-Token`.
3. The API validates that the Gateway session is active and specifically
   scoped to `flowpilot` with a `portfolio` parent session.
4. The API encrypts the bearer with `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` and
   stores ciphertext in PostgreSQL. The row expiry is capped by both the
   configured credential TTL and the Gateway session's remaining lifetime.
5. Redis/RQ receives only an opaque credential UUID as a job argument. The
   bearer is not put in a workflow payload, Redis job body, or log.
6. The worker atomically consumes the encrypted PostgreSQL row, decrypts the
   bearer in memory for that single job, and clears the ContextVar afterward.

Queued BYOK deliberately fails closed if PostgreSQL or the application
Fernet key is missing, the Gateway session is wrong-project/expired, or the
credential reference has already been consumed. If a job waits longer than
the credential or Gateway TTL, reopen the portfolio demo and queue it again.

## 8. Render demo settings

The Render blueprint runs API execution inline because it does not provision
the PostgreSQL/Redis/RQ queue stack. For its BYOK demo, set:

- `LLM_GATEWAY_URL` on the API service to `https://<your-gateway-host>/v1`;
- `PORTFOLIO_SESSION_GATEWAY_URL` on the UI service to
  `https://<your-gateway-host>` (without `/v1`);
- `CORS_ORIGINS` on the API service to the exact HTTPS Streamlit UI origin,
  not `*`.

No `FLOWPILOT_GATEWAY_CREDENTIAL_KEY` is needed for the inline Render demo,
because no Gateway token is serialized into a queue. For queued BYOK on a
persistent deployment, use the Docker Compose architecture (or provision
managed PostgreSQL and Redis/RQ) and configure the FlowPilot Fernet key in API
and worker.

## 9. Operational checks after the UI test

```bash
docker compose ps
docker compose logs --since=10m --tail=100 api worker scheduler litellm
```

Check the dashboards locally:

- Prometheus: `http://localhost:9090/targets`
- Grafana: `http://localhost:3000`
- Jaeger: `http://localhost:16686`

To confirm database-backed history remains after an API restart, create a run,
then run `docker compose restart api` and inspect Recent Executions again. Do
not delete Docker volumes during this check.

## Scope and completion criteria

This bundle updates FlowPilot and supplies the separate shared-Gateway
allowlist companion. It does not commit, push, or deploy either repository.
Do not consider BYOK complete until the shared Gateway allowlist is deployed
and a real FlowPilot handoff has produced a model-generated queued workflow.
