"""
Observability bootstrap — called once per service on startup.

Sets up:
  - Sentry: unhandled exception capture + performance tracing
  - Axiom via OTel: structured spans exported over OTLP HTTP

Usage:
    from shared.observability import setup
    setup("api")          # or "launcher" / "execution"
"""

from __future__ import annotations

import os


def setup(service_name: str) -> None:
    _init_sentry(service_name)
    _init_otel(service_name)


# ── Sentry ────────────────────────────────────────────────────────────────────

def _init_sentry(service_name: str) -> None:
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        return

    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.2,
        environment=os.getenv("ENV", "production"),
        release=service_name,
        send_default_pii=False,
    )


# ── Axiom via OpenTelemetry ───────────────────────────────────────────────────

def _init_otel(service_name: str) -> None:
    api_key = os.getenv("AXIOM_API_KEY", "")
    dataset = os.getenv("AXIOM_DATASET", "promptforge")
    if not api_key:
        return

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource(attributes={SERVICE_NAME: f"promptforge-{service_name}"})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(
        endpoint="https://api.axiom.co/v1/traces",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Axiom-Dataset": dataset,
        },
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def get_tracer(name: str):
    """Return an OTel tracer. Safe to call even if OTel was not initialised."""
    from opentelemetry import trace
    return trace.get_tracer(name)
