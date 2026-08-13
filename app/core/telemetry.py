import logging

from app.core.config import Settings

logger = logging.getLogger(__name__)


def configure_telemetry(settings: Settings) -> None:
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name, "deployment.environment": settings.app_env}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint)))
        trace.set_tracer_provider(provider)
        logger.info("otel_configured endpoint=%s", settings.otel_exporter_endpoint)
    except Exception as exc:
        # Observability must never prevent the application from starting.
        logger.warning("otel_setup_failed error=%s", exc)
