# FlowPilot Deployment Readiness

Audit snapshot: 15 November 2026. Default branch: `main`.

## Verified from the live repository

Latest commit: `c8bad65` — `fix: organize smoke test imports`.

Latest inspected GitHub Actions run: [CI #28](https://github.com/AsaifAli/AI-Automation-Command-Center/actions/runs/43744181012), successful.

- Ruff: all checks passed.
- Automated tests: 85 passed, 3 non-blocking deprecation warnings.
- Workflow evaluations: 4/4 passed.
- Python compile check: passed.
- `docker compose config --quiet`: passed.
- Application Docker image build: passed.

Earlier user-provided local Docker logs also showed the four workflow types completing and an outreach approval being recorded. Those local results are useful evidence, but they are not a live Render acceptance test.

## Not yet production-accepted

1. Tenant/session ownership is not enforced on run history or run lookup. The public UI shares a server-side operator credential and reads shared run history. Do not use the public demo for confidential multi-client data until this is fixed.
2. Public live inference has no verified per-session/user quota, concurrency limit, or spend ceiling.
3. Approval endpoint does not currently enforce completed-only decisions or prevent conflicting repeated decisions.
4. Queue dispatch and PostgreSQL run creation are not protected by a transactional outbox; stale `queued`/`running` jobs need recovery/reconciliation.
5. The durable Render Blueprint has not been shown deployed and acceptance-tested in this audit.
6. Production Blueprint sets `OTEL_ENABLED=false`; the local monitoring stack is not provisioned in that Render topology.
7. Database restore, queue restart recovery, and rollback have not been demonstrated against the deployed topology.
8. Shared Gateway project allowlist, live one-time BYOK handoff, encrypted credential persistence, worker consumption, and real model output still need live acceptance.
9. Root README was previously an internal smoke-test patch note and should be replaced by the bundled project README.
10. `main` branch protection is disabled. Require CI checks before merging/releasing.

## Deployment modes

### `render.yaml`

Two web services: API and UI. It uses inline execution and ephemeral run state. No Render PostgreSQL, persistent Key Value, RQ worker, or scheduler is provisioned. Treat it as a controlled portfolio demo, not a durable production service.

### `render.production.yaml`

Paid durable topology: API, UI, worker, scheduler, PostgreSQL, and persistent Key Value. It requires resource/cost review, secrets configuration, shared Gateway allowlist configuration, monitoring plan, backup plan, and all live acceptance tests in `docs/PRODUCTION_ACCEPTANCE.md`.

## Status language

Use **CI passing** for the current code state. Do not describe FlowPilot as a fully validated production deployment until the P0/P1 blockers in `docs/PRODUCTION_GAP_REGISTER.md` are closed and the live acceptance tests have evidence attached.
