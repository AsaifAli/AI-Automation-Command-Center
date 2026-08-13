## v3.3.3
- Fixed FastAPI response validation when persisted demo/LLM usage omitted required `provider` metadata.
- Normalized usage metadata at both the LLM provider and LangGraph workflow boundaries.
- Added regression coverage for usage metadata normalization.

## 3.3.0

- Added Docker Compose end-to-end smoke test covering all four workflows and outreach approval.
- Preserved the API-created run ID through LangGraph/worker execution for consistent tracing and persistence.
- Added regression coverage for run ID continuity.


## 3.0.2
- Fixed workflow persistence when evidence or output contains `datetime` values.
- Added JSON serialization for `datetime`, `date`, `Decimal`, and UUID values.
- Added a regression test covering datetime serialization.
# Changelog

## 3.0.0 — Portfolio Platform Upgrade

- Replaced embedded persistence with PostgreSQL.
- Added Redis/RQ asynchronous workflow execution.
- Added dedicated worker service.
- Added dedicated scheduler service with immediate startup trigger and recurring execution.
- Added Prometheus metrics endpoint.
- Added Grafana dashboard provisioning.
- Added OpenTelemetry tracing through an OTel Collector to Jaeger.
- Added Docker Compose service healthchecks and dependency conditions.
- Added worker scaling documentation.
- Updated Streamlit UI for asynchronous run polling.
- Added Compose-only deployment and troubleshooting documentation.
- Added stronger portfolio/interview documentation.
- Added CI validation for Docker Compose configuration.

## 3.0.1 — Docker image compatibility fix

- Fixed the Jaeger image reference from the non-existent `jaegertracing/all-in-one:1.74` tag to the published `1.76.0` tag.
- Jaeger 1.76.0 publishes both `linux/amd64` and `linux/arm64` images, making the stack suitable for Apple Silicon Macs as well as Intel/AMD systems.

## 3.3.2 - Streamlit API response hardening

- Prevented Streamlit from crashing when the API returns an empty or non-JSON response.
- Added explicit HTTP/status/body diagnostics for API failures.
- Added transient polling behavior for freshly queued runs so UI startup/API races do not produce `JSONDecodeError` crashes.
- Hardened health, metrics, queue, run-detail, and run-history response parsing.

## 3.3.2
- Added backward-compatible PostgreSQL schema migration statements so existing Docker volumes upgrade without requiring `down -v`.
- Hardened run lookup and run-history API endpoints to return actionable JSON errors instead of opaque 500 responses.
- Preserved existing workflow data during application upgrades.
