"""T3 — HTTP tracing, W3C propagation, log correlation and `GET /metrics`.

Covers the observability feature's API acceptance criteria: every request gets
a server span + `X-Trace-Id` + structlog `trace_id`/`span_id`, valid W3C
`traceparent` headers are adopted, malformed ones fall back to a fresh root
span, Prometheus labels use route templates only, and neither PII nor
high-cardinality identifiers reach spans, logs or metrics (ADR 0023).
"""

import re
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode
from prometheus_client.parser import text_string_to_metric_families

from app.main import app
from app.shared.errors import register_error_handlers
from app.shared.logging import RequestContextMiddleware, add_trace_context
from app.shared.telemetry import (
    FORBIDDEN_METRIC_LABELS,
    HTTP_IN_FLIGHT_LABELS,
    HTTP_REQUEST_LABELS,
    METRICS_PATH,
    WORKER_EVENT_LABELS,
    WORKER_UNIT_LABELS,
    TelemetryMiddleware,
    configure_telemetry,
    get_telemetry,
)
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)

TRACE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
SPAN_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
VALID_TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
VALID_PARENT_SPAN_ID = 0x00F067AA0BA902B7


@pytest.fixture(scope="session")
def span_exporter() -> InMemorySpanExporter:
    """Collect finished server spans from the app's global tracer provider."""
    exporter = InMemorySpanExporter()
    telemetry = configure_telemetry()
    telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture(autouse=True)
def _clear_spans(span_exporter: InMemorySpanExporter) -> Iterator[None]:
    span_exporter.clear()
    yield


def _labels_in(text: str) -> set[str]:
    labels: set[str] = set()
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            # `le` is the histogram bucket boundary, not one of our labels.
            labels.update(name for name in sample.labels if name != "le")
    return labels


def _span_for(exporter: InMemorySpanExporter, route: str) -> Any:
    matches = [
        span
        for span in exporter.get_finished_spans()
        if (span.attributes or {}).get("http.route") == route
    ]
    assert matches, f"no finished span with http.route={route!r}"
    return matches[-1]


async def test_requests_are_traced_with_headers_and_log_correlation(
    client_factory: ClientFactory,
    span_exporter: InMemorySpanExporter,
) -> None:
    # Force lazy loggers to resolve the capture configuration.
    structlog.configure(cache_logger_on_first_use=False)

    async with client_factory() as client:
        with structlog.testing.capture_logs(
            processors=[
                structlog.contextvars.merge_contextvars,
                add_trace_context,
            ]
        ) as logs:
            response = await client.get(
                "/healthz", headers={"X-Request-Id": "observability-test-request"}
            )

    assert response.status_code == 200
    trace_id = response.headers["X-Trace-Id"]
    assert TRACE_ID_PATTERN.fullmatch(trace_id)
    assert response.headers["X-Request-Id"] == "observability-test-request"

    span = _span_for(span_exporter, "/healthz")
    assert span.kind is SpanKind.SERVER
    assert format(span.context.trace_id, "032x") == trace_id
    assert span.status.status_code is StatusCode.UNSET

    request_log = next(entry for entry in logs if entry["event"] == "request")
    assert request_log["request_id"] == "observability-test-request"
    assert request_log["trace_id"] == trace_id
    assert request_log["span_id"] == format(span.context.span_id, "016x")


async def test_valid_traceparent_is_adopted_and_echoed(
    client_factory: ClientFactory,
    span_exporter: InMemorySpanExporter,
) -> None:
    async with client_factory() as client:
        response = await client.get(
            "/healthz", headers={"traceparent": VALID_TRACEPARENT}
        )

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"] == f"{VALID_TRACE_ID:032x}"

    span = _span_for(span_exporter, "/healthz")
    assert span.context.trace_id == VALID_TRACE_ID
    assert span.parent is not None
    assert span.parent.span_id == VALID_PARENT_SPAN_ID


async def test_malformed_traceparent_falls_back_to_a_fresh_root_span(
    client_factory: ClientFactory,
    span_exporter: InMemorySpanExporter,
) -> None:
    async with client_factory() as client:
        response = await client.get("/healthz", headers={"traceparent": "not-a-trace"})

    assert response.status_code == 200
    trace_id = response.headers["X-Trace-Id"]
    assert TRACE_ID_PATTERN.fullmatch(trace_id)
    assert trace_id != f"{VALID_TRACE_ID:032x}"
    assert trace_id != "0" * 32

    span = _span_for(span_exporter, "/healthz")
    assert format(span.context.trace_id, "032x") == trace_id
    assert span.parent is None


async def test_http_metrics_use_route_templates_and_never_raw_paths(
    client_factory: ClientFactory,
) -> None:
    employee_id = "0190a3de-6b1f-7c2a-9f00-5f0e7d1a2b3c"
    unknown_slug = "unknown-random-slug-0190a3de"

    async with client_factory() as client:
        missing = await client.get(f"/api/v1/employees/{employee_id}")
        unmatched = await client.get(f"/api/v1/{unknown_slug}")
        first_scrape = await client.get(METRICS_PATH)
        second_scrape = await client.get(METRICS_PATH)

    assert missing.status_code == 404
    assert unmatched.status_code == 404
    assert first_scrape.status_code == 200
    assert first_scrape.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "workpulse_http_requests_total" in first_scrape.text
    assert "workpulse_http_request_duration_seconds" in first_scrape.text
    assert 'route="/api/v1/employees/{id}"' in first_scrape.text
    assert 'route="UNMATCHED"' in first_scrape.text
    assert employee_id not in first_scrape.text
    assert unknown_slug not in first_scrape.text

    # `/metrics` is excluded from self-instrumentation: the scrape after it
    # must not contain a `/metrics` route series.
    assert 'route="/metrics"' not in second_scrape.text


async def test_metrics_and_spans_expose_no_tenant_pii_or_identifiers(
    client_factory: ClientFactory,
    span_exporter: InMemorySpanExporter,
) -> None:
    async with client_factory() as tenant_a:
        refs = await create_org_refs(tenant_a)
        created = (
            await tenant_a.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Zorblax",
                    last_name="Qqweird",
                    email="pii-unique@example.test",
                ),
            )
        ).json()

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        cross_tenant = await tenant_b.get(f"/api/v1/employees/{created['id']}")

    # `/metrics` is unauthenticated operational plumbing, like `/healthz`.
    async with client_factory(None) as anonymous:
        scrape = await anonymous.get(METRICS_PATH)

    assert cross_tenant.status_code == 404
    assert scrape.status_code == 200

    labels = _labels_in(scrape.text)
    # Workers share this process (and therefore the telemetry registry) in
    # integration tests; production workers expose the same bounded labels on
    # their own scrape ports. Only the contract's label set is allowed either
    # way, and never an identifier or PII value.
    assert labels <= (
        set(HTTP_REQUEST_LABELS)
        | set(HTTP_IN_FLIGHT_LABELS)
        | set(WORKER_EVENT_LABELS)
        | set(WORKER_UNIT_LABELS)
    )
    assert labels.isdisjoint(FORBIDDEN_METRIC_LABELS)

    text = scrape.text
    for forbidden_label in (
        "tenant_id=",
        "employee_id=",
        "user_id=",
        "request_id=",
        "trace_id=",
    ):
        assert forbidden_label not in text

    # Span attributes and names are PII/identifier-free too (route templates,
    # methods and status codes only).
    spans = " ".join(
        f"{span.name} {dict(span.attributes or {})}"
        for span in span_exporter.get_finished_spans()
    )
    for value in (
        str(TENANT_A),
        str(TENANT_B),
        created["id"],
        "Zorblax",
        "Qqweird",
        "pii-unique@example.test",
    ):
        assert value not in text
        assert value not in spans


async def test_error_requests_record_error_status_and_balance_in_flight(
    span_exporter: InMemorySpanExporter,
) -> None:
    # Production-shaped mini app (debug=False): unhandled 500s are rendered by
    # ServerErrorMiddleware, outside the user middleware stack.
    mini_app = FastAPI()
    register_error_handlers(mini_app)
    mini_app.add_middleware(RequestContextMiddleware)
    mini_app.add_middleware(TelemetryMiddleware)

    @mini_app.get("/boom")
    def boom() -> None:
        raise RuntimeError("super-secret-value")

    structlog.configure(cache_logger_on_first_use=False)
    transport = ASGITransport(app=mini_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with structlog.testing.capture_logs(
            processors=[
                structlog.contextvars.merge_contextvars,
                add_trace_context,
            ]
        ) as logs:
            response = await client.get("/boom")

    assert response.status_code == 500
    trace_id = response.headers["X-Trace-Id"]
    assert TRACE_ID_PATTERN.fullmatch(trace_id)
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }

    # The failed request is still logged with its trace correlation.
    request_log = next(entry for entry in logs if entry["event"] == "request")
    assert request_log["status_code"] == 500
    assert request_log["trace_id"] == trace_id
    assert SPAN_ID_PATTERN.fullmatch(request_log["span_id"])

    span = _span_for(span_exporter, "/boom")
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["http.response.status_code"] == 500
    assert span.attributes["error.type"] == "RuntimeError"
    assert format(span.context.trace_id, "032x") == trace_id
    assert "super-secret-value" not in str(dict(span.attributes or {}))

    telemetry = get_telemetry()
    assert telemetry is not None
    metrics_text = telemetry.metrics_text().decode()
    assert 'workpulse_http_requests_in_flight{method="GET"} 0.0' in metrics_text


def test_otlp_export_is_disabled_unless_configured() -> None:
    telemetry = get_telemetry()
    assert telemetry is not None
    assert telemetry.otlp_export_enabled is False


def test_metrics_endpoint_is_hidden_from_openapi() -> None:
    assert METRICS_PATH not in app.openapi()["paths"]
