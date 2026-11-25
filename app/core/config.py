
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Automation Command Center"
    app_version: str = "3.5.0"
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Infrastructure
    database_url: str = ""
    redis_url: str = "redis://redis:6379/0"
    queue_name: str = "automation"
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = Field(default="", repr=False)

    # Demo / LLM
    demo_mode: bool = False
    llm_provider: str = "litellm"
    openai_api_key: str = ""
    openai_base_url: str = "http://litellm:4000/v1"
    openai_model: str = "portfolio-free"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_gateway_url: str = ""
    request_timeout_seconds: int = 30
    max_retries: int = 2
    inline_execution: bool = False
    # Queue BYOK tokens only as encrypted PostgreSQL records; RQ stores UUID references.
    gateway_job_credential_encryption_key: str = Field(
        default="",
        validation_alias="FLOWPILOT_GATEWAY_CREDENTIAL_KEY",
    )
    gateway_job_credential_ttl_seconds: int = 900

    # API / security
    api_base_url: str = "http://localhost:8000"
    api_auth_enabled: bool = False
    # API processes require configured credentials. Internal worker/scheduler
    # processes still keep production auth enabled but do not serve API routes.
    api_auth_require_keys: bool = True

    # Existing dashboard key: retained as admin.
    api_key: str = ""

    # Role-specific API credentials.
    api_viewer_keys: str = ""
    api_operator_keys: str = ""
    api_reviewer_keys: str = ""
    api_admin_keys: str = ""

    # Server-assigned audit identities.
    api_reviewer_name: str = "Configured Reviewer"
    api_admin_name: str = "API administrator"

    cors_origins: str = "http://localhost:8501"
    max_payload_bytes: int = 1_000_000
    # Write quotas are shared through Redis in production and process-local in development.
    api_write_ip_rate_limit_per_minute: int = 60
    api_write_principal_rate_limit_per_minute: int = 20
    api_rate_limit_window_seconds: int = 60

    # Intelligence
    demo_config_path: str = "config.json"
    intelligence_timeout_seconds: int = 10
    max_intelligence_items: int = 25

    # Cost approximation per 1M tokens
    input_cost_per_million: float = 0.15
    output_cost_per_million: float = 0.60

    # Scheduler
    schedule_enabled: bool = False
    schedule_interval_minutes: int = 60
    schedule_workflow: str = "competitor"
    schedule_competitors: str = "Example Protocol,Example AI Startup"

    # Observability
    otel_enabled: bool = True
    otel_service_name: str = "ai-automation-api"
    otel_exporter_endpoint: str = "http://otel-collector:4318/v1/traces"

    @model_validator(mode="after")
    def validate_api_credentials(self):
        if not 60 <= self.gateway_job_credential_ttl_seconds <= 1800:
            raise ValueError(
                "GATEWAY_JOB_CREDENTIAL_TTL_SECONDS must be between 60 and 1800"
            )
        if self.gateway_job_credential_encryption_key.strip():
            try:
                Fernet(self.gateway_job_credential_encryption_key.strip().encode("ascii"))
            except (TypeError, ValueError, UnicodeEncodeError) as exc:
                raise ValueError(
                    "FLOWPILOT_GATEWAY_CREDENTIAL_KEY must be a valid Fernet key"
                ) from exc
        if self.app_env.strip().lower() in {"prod", "production"} and not self.api_auth_enabled:
            raise ValueError("API_AUTH_ENABLED must be true in production")

        role_values = {
            "viewer": self.api_viewer_keys,
            "operator": self.api_operator_keys,
            "reviewer": self.api_reviewer_keys,
            # Legacy API_KEY is the dashboard/admin credential.
            "admin": ",".join(
                value
                for value in (
                    self.api_admin_keys,
                    self.api_key,
                )
                if value
            ),
        }

        configured: dict[str, str] = {}

        for role, raw_values in role_values.items():
            for candidate in (
                item.strip()
                for item in raw_values.split(",")
            ):
                if not candidate:
                    continue

                previous_role = configured.get(candidate)

                if previous_role and previous_role != role:
                    raise ValueError(
                        "API role credentials must be unique across roles"
                    )

                configured[candidate] = role

        if self.api_auth_enabled and self.api_auth_require_keys and not configured:
            raise ValueError(
                "API_AUTH_ENABLED requires at least one configured API role key"
            )

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
