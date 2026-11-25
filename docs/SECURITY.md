# Security Review — AI Automation Command Center (FlowPilot)

This is a source/config review, not a penetration test or production certification. The Command Center is a privileged orchestration control plane: it accepts workflow requests, reads/writes run history, handles Gateway BYOK sessions, and can stage external-impact actions behind human approval.

## Existing controls observed
- Server-side role authorization in `app/core/auth.py`: viewer, operator, reviewer and admin permissions are distinct; role is derived from configured API key using constant-time comparison.
- Production settings require API authentication and configured role keys (`app/core/config.py`); Render production blueprint generates API role credentials.
- CORS is configured from explicit origins and does not enable credentialed cross-origin requests (`app/api/main.py`).
- Request bodies have a configured size limit; responses receive `nosniff`, frame-deny and no-store headers.
- Gateway BYOK sessions are checked against the FlowPilot project audience. Queued job credentials are encrypted in PostgreSQL and RQ receives an opaque reference, not the token; inline credentials are request-scoped and cleared.
- Intelligence feed fetching has SSRF defenses (DNS/private-address checks, disabled redirects, disabled environment proxy trust, response-size limits); regression coverage exists in `tests/test_intelligence_security.py`.
- PostgreSQL and Redis are private in the production Render blueprint; role keys and encryption keys are generated/configured as deployment secrets.
- Content/outreach actions are paused behind reviewer approval; the authenticated principal, not a client-supplied reviewer name, is used for audit identity.

## Findings requiring attention
1. **Log redaction (Medium/High): PARTIALLY PATCHED in this branch.** `app/core/logging.py` redacts common bearer tokens, API/access/refresh keys, passwords and OpenAI-style keys before JSON serialization. `tests/test_logging_security.py` covers representative log messages. This pattern-based filter is defense in depth, not a substitute for never logging request bodies, prompts or credentials; broader call-site review remains required.
2. **Rate limiting (High): PATCHED with deployment-mode caveat.** Mutating `/api/v1/*` routes have per-IP and API-key-fingerprint quotas (`app/core/rate_limit.py`). The active free-tier `render.yaml` defines inline execution, so that deployment uses bounded in-process counters on its single API instance. The queued-worker topology in `render.production.yaml` uses Redis-backed atomic fixed-window counters and fails closed if Redis is unavailable. Add edge/WAF limits as an additional perimeter control.
3. **Read authorization / BOLA boundary (High where multiple principals are untrusted):** `runs:read` grants access to the global run list and `get_run(run_id)`; there is no per-user or tenant ownership field in the observed API path. This may be intentional for a single-owner private agency OS, but must not be presented as multi-tenant isolation. Keep the API private and add owner/tenant scoping before sharing across customers.
4. **Gateway URL validation (High):** Gateway verification uses an environment-configured URL and disables redirects/proxy trust, but validate that production URL is HTTPS and matches the approved Gateway host. Never accept it from workflow payloads.
5. **Production readiness leakage (Low/Medium):** readiness catches exceptions and logs stack traces; response is generic, which is appropriate. Restrict diagnostic logs to operators and ensure secrets are redacted.
6. **MFA, database grants and secret rotation are external controls.** The repository cannot turn on provider/GitHub/hosting MFA or validate actual secret values.

## 20-control assessment
| # | Control | Result |
|---|---|---|
|01|SQL injection|PARTIAL — repository persistence uses database abstraction; audit every query in `app/storage/repository.py` and keep values parameterized|
|02|XSS|PARTIAL — Streamlit UI and model-generated content require safe rendering; review all unsafe HTML and dynamic Markdown|
|03|CSRF|PARTIAL — API uses header API keys, not browser cookies; verify Streamlit/proxy origins and cookie behavior|
|04|File upload|N/A for the observed FastAPI API route set; validate any future document/upload workflow inputs before parsing|
|05|SSRF|PASS for intelligence feeds — DNS/private-address checks, redirects disabled, trust_env false and response size cap have regression tests; Gateway URL is a separate configured endpoint|
|06|BOLA/IDOR|PARTIAL — run IDs are globally readable by authorized roles; safe only for the single-owner private control-plane model|
|07|Rate limiting|PARTIAL — IP + API-key-fingerprint quotas cover mutating routes; current inline demo uses process-local counters, while queued production topology uses Redis-shared counters. Edge/WAF limits remain recommended|
|08|Password hashing|N/A — no local username/password login|
|09|MFA|EXTERNAL — enable for GitHub, Render, database, Redis and provider administrator accounts|
|10|Server-side permissions|PASS — route permission dependencies and role allowlists are enforced in the API|
|11|RLS|PARTIAL/N/A — PostgreSQL is used but no per-tenant RLS policy was observed; single-owner model should use least-privilege DB role/private network|
|12|JWT secret|GATEWAY-OWNED — FlowPilot validates Gateway JWT through the Gateway status endpoint rather than issuing JWTs itself|
|13|Server-side API keys|PASS — Render API and Gateway secrets are server-side environment values; do not inject reviewer/admin keys into UI|
|14|No localStorage tokens|PARTIAL — Gateway bearer is forwarded from app session state; verify browser storage in Streamlit and any custom UI|
|15|No default credentials|PASS/PARTIAL — Render generates role secrets and compose requires passwords; verify no production deployment overrides them with placeholders|
|16|Strict CORS|PASS with deployment config — explicit `CORS_ORIGINS`, credentials disabled; production Render value must be set to exact trusted origin(s)|
|17|Webhook signatures|N/A — no inbound third-party webhook receiver identified|
|18|Source maps|N/A — Streamlit app, no client JS source-map build|
|19|Clean production logs|PARTIAL — common credential patterns are redacted in JSON logging and tested; prohibit raw payload logging and extend PII review|
|20|Dependency patching|UNVERIFIED — run resolver-aware Python audit and container image scan against the deployment image|

## Active deployment topology
- The connected free-tier API service is deployed from `render.yaml`, which selects inline execution and ephemeral in-memory run state. In this mode, quotas are process-local and reset on instance restart; the current service is one instance.
- The separate `render.production.yaml` describes a PostgreSQL + Redis + worker topology. Its Redis-backed quota path applies when queued execution is enabled; do not assume those resources exist in the free-tier service.

## External deployment actions
- Confirm Render production `CORS_ORIGINS` contains only the actual UI origin(s).
- Keep PostgreSQL/Redis private; use unique DB credentials and least-privilege DB roles.
- Configure generated API role keys and the Fernet Gateway credential key in the secret manager. Rotate any key that has ever appeared in a log, prompt or commit.
- Enable MFA on GitHub, Render, database, Redis and AI provider accounts.
- Ensure Gateway base URL is HTTPS and points only to the approved Gateway service.
- Keep the public UI separate from the API. Never inject reviewer/admin credentials into a browser-facing service.
- Run dependency and container scanning on the exact deployed images, not only source manifests.

## AI audit prompt
Act as a Principal Application Security Engineer. Audit all 20 controls against actual code and deployment manifests. Mark PASS, PARTIAL, FAIL or N/A, cite exact paths and line numbers, and do not infer a pass from absence of a match. For each vulnerability, explain preconditions and impact, provide the minimal safe patch, and add a regression test. Distinguish the single-owner private agency threat model from a multi-tenant SaaS threat model. Never print secrets, authorization headers, user prompts or customer content.