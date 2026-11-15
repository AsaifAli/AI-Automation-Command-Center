# FlowPilot 3.5.0 — Hardened Release Guide

## Release scope

This release hardens the client-facing Streamlit experience and deployment hygiene while preserving the FlowPilot workflow backend.

- Client-facing results are rendered as channel cards, evidence views, KPI tables, outreach drafts and downloadable Markdown reports. Raw JSON is not rendered as the primary client output.
- Content briefs are entered one per line; commas and punctuation are preserved. Up to five briefs and the selected channels are validated before a run is queued.
- The live UI labels BYOK and service-level LLM modes separately. BYOK cost is shown as provider-billed rather than presenting a misleading service-rate estimate.
- Client-visible failure messages are sanitized; diagnostic exceptions stay in service logs.
- The Gateway exchange validates `target_project == "flowpilot"` (not a missing/optional field).
- Queue credentials are encrypted in PostgreSQL and consumed once by a worker. RQ receives only an opaque credential UUID.
- `requirements.txt` contains runtime dependencies; test and lint tooling is in `requirements-dev.txt` and is no longer installed in the production image. The unused LangChain OpenAI adapter was removed.
- Scheduled runs are disabled by default. `make clean` preserves Docker volumes; data deletion is explicit via `make clean-volumes`.
- `scripts/bootstrap_env.py` creates a private local `.env` and Prometheus token, but refuses to overwrite either if it already exists. The Prometheus token is mounted as a Compose secret rather than a world-readable bind-mounted file.
- `.venv312`, caches, secrets, and tests are excluded from the Docker build context.

## Apply to an existing checkout

1. Make a backup of the current checkout.
2. Extract this release ZIP into the existing FlowPilot repository root. The archive intentionally does not contain `.git`, `.env`, `secrets/`, or virtual environments, so those stay in your checkout.
3. Inspect `git status --short`. Never add `.env`, `secrets/`, database files, or local backups to Git.
4. Keep the existing `.env`. Do not run the bootstrap script if `.env` or `secrets/prometheus-token` already exists.

## Local validation (Python 3.12)

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q app ui tests scripts
ruff check app ui tests scripts
python -m pytest -q
PYTHONPATH=. python scripts/evaluate.py
docker compose config --quiet
docker compose build
```

Then start and smoke-test the durable local Compose topology:

```bash
docker compose up -d

docker compose ps
python scripts/smoke_test.py --base-url http://localhost:8000 --timeout 120
```

Do not use `docker compose down -v` during routine verification; it destroys database, queue, and observability volumes.

## Secrets and credentials

- The earlier project archive contained local environment/monitoring credential files, and a Fernet key was pasted during troubleshooting. Treat any real values from those materials as exposed and rotate them.
- Rotate real OpenRouter/Gemini provider keys, LiteLLM master key, API role keys, database/Grafana passwords, Prometheus bearer, and any Fernet encryption key that was shared.
- For queued BYOK, API and worker must use the same `FLOWPILOT_GATEWAY_CREDENTIAL_KEY`. Rotate it only after pending credential rows/jobs have drained or been expired, because old ciphertext cannot be decrypted with the new key.
- The `.env` bootstrap refuses to overwrite existing settings. If `.env` already exists, update it manually and preserve the working values that are not changing.

## Shared Portfolio LLM Gateway (separate repository)

FlowPilot's project-bound single-use handoff is rejected by the shared Gateway until the Gateway allowlist includes `flowpilot`.

Use the separate `FlowPilot-Shared-Gateway-Companion-v1.zip` in the `shared-Portfolio-LLM-Gateway` checkout, not in this repository. Apply the companion, run the Gateway handoff tests, push/deploy that Gateway change first, then test FlowPilot.

## Render deployment modes

### Existing `render.yaml` — current portfolio/demo deployment

This remains the low-cost two-service API + UI deployment. It uses inline execution (`INLINE_EXECUTION=true`) and does not provision PostgreSQL, Redis/Key Value, or RQ worker services. State is ephemeral. It can test inline BYOK and real inference after the shared Gateway allowlist and environment variables are configured, but it is not a durable queued-workflow topology.

### `render.production.yaml` — separate durable/paid topology

This is an alternate Blueprint. It provisions API, UI, worker, scheduler, PostgreSQL and private persistent Key Value. It is designed for queued BYOK with durable credential storage. It uses paid Render plans and must be reviewed for cost and service ownership before syncing. Do not have two Blueprints manage the same Render resources.

The current Render schema accepts service plans such as `starter`, Key Value plans such as `basic-256mb`, PostgreSQL plans such as `basic-1gb`, and the `checksPass` auto-deploy trigger. The Blueprint also uses valid PostgreSQL major-version and disk-size values. See the official schema before syncing: https://render.com/schema/render.yaml.json.

Required production settings:

- UI: `PORTFOLIO_SESSION_GATEWAY_URL=https://<gateway-origin>` (origin only; no `/v1`).
- API and worker: `LLM_GATEWAY_URL=https://<gateway-origin>/v1`.
- API: `CORS_ORIGINS=https://<exact-ui-origin>`; do not use `*`.
- API and worker: same durable PostgreSQL database, same Redis/Key Value, and same Fernet `FLOWPILOT_GATEWAY_CREDENTIAL_KEY`.
- UI receives the operator role only. Reviewer/admin keys must not be injected into the public UI; authorized reviewers use the time-limited reviewer credential control.
- `SCHEDULE_ENABLED` is false by default. Enable it only after reviewing the workflow, targets, and schedule.

## Production acceptance tests

1. Confirm the shared Gateway reports the expected project-bound single-use exchange and returns `target_project=flowpilot`.
2. Confirm FlowPilot checks session status and requires `project=flowpilot` and `session_project=portfolio`.
3. Queue a BYOK run in the durable topology. Verify that Redis/RQ contains only an opaque credential reference, not a bearer.
4. Verify PostgreSQL stores ciphertext, the worker atomically consumes it once, and expiry/cleanup behave as expected.
5. Generate a complete content pack through the live model. Verify provider/model/token metadata and provider-billed BYOK pricing labels.
6. Confirm every external-facing draft remains `needs_review` / `human_approval_required`; no publish/send occurs without an authorized approval.
7. Restart API/worker and verify durable history, approvals, audit events, and queue processing.
8. Verify Render health checks and Prometheus/Grafana/Jaeger/OTel observability for the selected deployment topology.

## Git release

The connected GitHub write integration rejected branch creation in this session. No repository push or Render deployment has been performed from this release task.

After applying and validating the release locally:

```bash
git status --short
# Confirm .env, secrets/, .venv312/, caches and backups are not staged.
git switch -c hardening/flowpilot-3.5.0
git add -A
git diff --cached --check
git status --short
git commit -m "Harden FlowPilot 3.5.0 client UX and BYOK"
git push -u origin hardening/flowpilot-3.5.0
```

Open a PR and merge to the branch linked by Render after CI passes. Render auto-deploys only when the service is linked to that branch and its auto-deploy setting is enabled. Review the Render dashboard and deployment logs before running production acceptance tests.
