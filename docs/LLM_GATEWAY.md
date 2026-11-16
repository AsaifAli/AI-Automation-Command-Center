# LLM Gateway and BYOK

FlowPilot supports two live inference paths:

- **Portfolio BYOK:** the user connects a provider in the portfolio, which launches FlowPilot with a short-lived, project-scoped handoff code.
- **Local service key:** the local Docker Compose stack can route through its LiteLLM proxy when a provider key has been configured in `.env`.

## Portfolio handoff

The handoff is project-bound and single-use. FlowPilot removes the `portfolio_llm_handoff` query parameter immediately, posts the code to the shared Gateway exchange endpoint from the Streamlit server, validates `target_project=flowpilot`, and stores the returned short-lived bearer only in Streamlit server-side session state. The old reusable `portfolio_llm_session` query parameter is rejected.

The API receives the session only through `X-LLM-Gateway-Token` and checks the Gateway status response for:

- `active=true`
- `project=flowpilot`
- `session_project=portfolio`
- a valid, sufficiently long remaining expiry

The current shared Gateway status endpoint returns both `project` and `session_project`. The former is the delegated demo project; the latter is the original portfolio session. Keep this contract aligned if the Gateway response schema changes.

Configure:

```dotenv
PORTFOLIO_SESSION_GATEWAY_URL=https://<gateway-origin>
LLM_GATEWAY_URL=https://<gateway-origin>/v1
```

The UI uses the origin for `/v1/demo/session/exchange`. The API uses the `/v1` base for `/demo/session/status` and `/chat/completions`.

## Queued local BYOK

For the local queued topology, the API validates the Gateway bearer, encrypts it with `FLOWPILOT_GATEWAY_CREDENTIAL_KEY`, and stores the ciphertext with a short TTL. RQ receives only an opaque credential reference. A worker consumes the reference, decrypts the bearer in memory for the LLM request, then clears the request context.

Generate a Fernet key when setting up the local queued path:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use the same key in the API and worker environments. Never commit `.env` or share the generated key. The public Render demo uses inline execution and does not require the queued-worker credential-storage path.

## Local LiteLLM

The Compose topology exposes an OpenAI-compatible LiteLLM endpoint to the API. Configure the applicable provider credentials in `.env`. The configured OpenRouter model may dynamically select an available model, and the Gemini route is a fallback only when its key is configured. A missing fallback key can make fallback requests fail even when the primary route is healthy.

For content workflows that return structured multi-channel output, a 30-second per-attempt timeout proved too short in the observed local run. Use the recommended local settings:

```dotenv
REQUEST_TIMEOUT_SECONDS=90
MAX_RETRIES=1
```

The smoke test waits up to 240 seconds for each queued workflow by default so it can observe a completed result or a real failure instead of timing out before the worker finishes.
