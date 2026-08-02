"""OpenTelemetry instrumentation and trace correlation utilities for Kalanjiyam."""

import logging
import os
from typing import Optional

LOG = logging.getLogger(__name__)

_OTEL_INITIALIZED = False


def get_current_trace_id() -> Optional[str]:
    """Return the active OpenTelemetry trace ID as a hex string, or None if unavailable."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            trace_id_int = span.get_span_context().trace_id
            return f"{trace_id_int:032x}"
    except Exception:
        pass
    return None


def init_opentelemetry(app) -> bool:
    """Initialize OpenTelemetry tracer provider, Flask instrumentation, and Celery instrumentation."""
    global _OTEL_INITIALIZED
    if _OTEL_INITIALIZED:
        return True

    if os.getenv("OTEL_SDK_DISABLED", "false").lower() in ("true", "1"):
        LOG.info("OpenTelemetry is explicitly disabled via OTEL_SDK_DISABLED.")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME

        resource = Resource(attributes={SERVICE_NAME: "kalanjiyam"})
        provider = TracerProvider(resource=resource)

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                LOG.info(f"OpenTelemetry OTLP exporter configured for endpoint: {otlp_endpoint}")
            except Exception as ex:
                LOG.warning(f"Failed to initialize OTLP exporter for {otlp_endpoint}: {ex}")

        trace.set_tracer_provider(provider)

        # Instrument Flask
        try:
            from opentelemetry.instrumentation.flask import FlaskInstrumentor
            FlaskInstrumentor().instrument_app(app)
            LOG.info("OpenTelemetry Flask instrumentation active.")
        except Exception as ex:
            LOG.warning(f"Failed to instrument Flask with OpenTelemetry: {ex}")

        # Instrument Celery
        try:
            from opentelemetry.instrumentation.celery import CeleryInstrumentor
            CeleryInstrumentor().instrument()
            LOG.info("OpenTelemetry Celery instrumentation active.")
        except Exception as ex:
            LOG.warning(f"Failed to instrument Celery with OpenTelemetry: {ex}")

        _OTEL_INITIALIZED = True
        return True

    except Exception as e:
        LOG.warning(f"OpenTelemetry initialization failed (graceful fallback): {e}")
        return False
