from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Automation Command Center"
    app_version: str = "3.0.0"
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Infrastructure
    database_url: str = "postgresql://automation:automation@postgres:5432/automation"
    redis_url: str = "redis://redis:6379/0"
    queue_name: str = "automation"

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

    # API / security
    api_base_url: str = "http://localhost:8000"
    api_auth_enabled: bool = False
    api_key: str = ""
    cors_origins: str = "http://localhost:8501"
    max_payload_bytes: int = 1_000_000

    # Intelligence
    demo_config_path: str = "config.json"
    intelligence_timeout_seconds: int = 10
    max_intelligence_items: int = 25

    # Cost approximation per 1M tokens
    input_cost_per_million: float = 0.15
    output_cost_per_million: float = 0.60

    # Scheduler
    schedule_enabled: bool = True
    schedule_interval_minutes: int = 60
    schedule_workflow: str = "competitor"
    schedule_competitors: str = "Example Protocol,Example AI Startup"

    # Observability
    otel_enabled: bool = True
    otel_service_name: str = "ai-automation-api"
    otel_exporter_endpoint: str = "http://otel-collector:4318/v1/traces"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
