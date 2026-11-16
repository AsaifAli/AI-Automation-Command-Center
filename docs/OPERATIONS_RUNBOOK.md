# FlowPilot Operations Runbook

## Service health

- API liveness: `/health` — process responds.
- API readiness: `/ready` — required database/Redis dependencies available for the selected topology.
- Streamlit health: `/_stcore/health`.
- Authenticated app metrics: `/metrics`.
- Prometheus scrape endpoint: `/prometheus` (bearer-token protected when API auth is enabled).

Do not use a successful liveness probe as proof that the queue, database, LLM provider, or Gateway is healthy.

## First response to an incident

1. Record UTC time, release SHA, service/deployment identifier, request ID, and affected workflow IDs.
2. Check API, worker, Redis/Key Value, and PostgreSQL health separately.
3. Check recent error types, queue age/depth, failed jobs, worker restart count, provider latency and provider error codes.
4. Do not print or copy secrets, BYOK bearers, uploaded content, or raw request bodies into an incident ticket.
5. Stop/disable scheduled triggers if they are generating repeated work; do not delete the database/queue volumes.
6. Preserve sanitized logs and relevant audit event IDs.

## Stuck queued/running run

The current release still needs durable outbox/reconciliation implementation. Until that ships, do not manually replay arbitrary RQ jobs without checking the matching PostgreSQL row and job ID. Avoid duplicate external actions. Once recovery is implemented, follow the runbook added by that change.

## Provider outage

- Check provider status and LiteLLM/Gateway health.
- Preserve the run reference and error type; do not expose provider response bodies to public users.
- Verify retry count and timeout remain bounded.
- Avoid raising timeouts globally just to mask provider degradation.
- Pause scheduled runs if retries amplify provider load.

## Database backup and restore

The current repository does not include a verified production backup/restore result. Before launch, choose a supported managed backup method, retention period, encryption policy, and restore target. Run a restore drill into an isolated database. Verify counts and samples for workflow runs, approvals, and audit events. Record restore duration and data-loss window. Never test restoration over the live database.

## Key rotation

- Rotate provider and API keys in the provider/platform first, then update service secrets.
- For the BYOK Fernet key, drain/expire pending encrypted credentials before rotating; old ciphertext cannot be decrypted by the new key.
- Verify API and worker use the same key after rotation.
- Re-run the BYOK acceptance test with a new handoff.

## Rollback

1. Identify last known-good commit and deployment.
2. Confirm DB schema compatibility before reverting application image.
3. Stop scheduled triggers if necessary.
4. Roll back API/UI/worker as a coordinated version set.
5. Confirm readiness, authenticated metrics, worker queue, and one safe test run.
6. Record any jobs stranded during rollback and reconcile them without duplicate side effects.

## Required operational decisions before production

- RPO and RTO.
- Database backup frequency and retention.
- Run/evidence/audit retention and deletion semantics.
- Queue retry/dead-letter policy.
- On-call owner and escalation path.
- Provider budget and alert thresholds.
