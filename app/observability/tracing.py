"""OpenTelemetry tracing configuration.

Instruments FastAPI, asyncpg, and httpx with OTLP export.
Configuration is driven by environment variables:
  - OTEL_EXPORTER_OTLP_ENDPOINT: gRPC endpoint for the OTLP collector
  - OTEL_SERVICE_NAME: logical service name (default: "whatsapp-sales-agent")
  - OTEL_TRACES_SAMPLER: sampler type (default: "parentbased_always_on")

If OTEL_EXPORTER_OTLP_ENDPOINT is not set, tracing is a no-op.
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def configure_tracing() -> None:
    """Configure OpenTelemetry tracing with OTLP exporter.

    Instruments FastAPI, asyncpg, and httpx automatically.
    Gracefully does nothing if OTEL_EXPORTER_OTLP_ENDPOINT is not configured
    or if OpenTelemetry packages are unavailable.
    """
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set; tracing disabled.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError as exc:
        logger.warning(
            "OpenTelemetry packages not available; tracing disabled: %s", exc
        )
        return

    service_name = os.environ.get("OTEL_SERVICE_NAME", "whatsapp-sales-agent")

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)

    # Instrument libraries
    FastAPIInstrumentor.instrument()
    AsyncPGInstrumentor().instrument()
    HTTPXClientInstrumentor.instrument()

    logger.info(
        "OpenTelemetry tracing configured: endpoint=%s, service=%s",
        otlp_endpoint,
        service_name,
    )
