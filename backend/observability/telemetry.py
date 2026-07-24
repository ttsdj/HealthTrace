from __future__ import annotations

import os

_status = {
    "enabled": False,
    "configured": False,
    "reason": "OTEL_EXPORTER_OTLP_ENDPOINT is not configured",
}


def configure_telemetry(app) -> dict:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return dict(_status)
    _status.update(configured=True)
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": "healthtrace-api"})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        _status.update(enabled=True, reason="")
    except Exception as exc:
        _status.update(enabled=False, reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    return dict(_status)


def telemetry_status() -> dict:
    return dict(_status)

