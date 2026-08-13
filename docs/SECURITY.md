# Security & Production Hardening

## Implemented in this portfolio version

- Secrets are supplied through environment variables rather than source code.
- `.env` is ignored by Git and excluded from the Docker build context.
- API endpoints use Pydantic validation.
- Payload size is bounded by middleware.
- Optional API-key authentication protects workflow endpoints.
- CORS is configurable rather than open by default.
- Security response headers are added by API middleware.
- Application containers run as a non-root user.
- Outreach side effects are blocked behind human approval.
- Workflow state and approvals are auditable in PostgreSQL.
- Redis and PostgreSQL are not exposed to the host by default.

## Production recommendations

### Authentication

Replace the optional static API key with OAuth/OIDC and role-based access control.

Suggested roles:

- `viewer`: read runs/metrics
- `operator`: execute workflows
- `reviewer`: approve/reject external-impact workflows
- `admin`: manage integrations/configuration

### Secrets

Use a cloud secret manager rather than `.env` for production credentials.

### Network security

Put the API/UI behind a TLS reverse proxy or managed load balancer. Keep PostgreSQL, Redis, Jaeger and the OTel collector on private networks.

### LLM safety

Add:

- prompt-injection detection for retrieved content
- source trust policies
- output schema validation
- PII redaction
- model allowlists
- spend limits
- per-user/per-workflow rate limits

### External actions

Any action capable of sending, publishing, transferring funds or modifying external systems should be modeled as a separate capability with an explicit authorization policy.

## Threat model discussion points

### Prompt injection

Competitor/news content is untrusted input. A production connector should sanitize and classify retrieved content before passing it into tool-enabled agents.

### Hallucinated evidence

Evidence should originate from observed source records. Generated reasoning should be labeled as inference rather than presented as an observation.

### Credential leakage

Never place provider credentials in prompts, logs, database payloads or Streamlit state.

### Queue abuse

Production deployments should add authentication, rate limits, queue depth limits and per-workflow concurrency policies.
