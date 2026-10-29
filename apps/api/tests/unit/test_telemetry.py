"""Shared telemetry unit tests (feature rule 5, ADR 0023).

Covers W3C id formats, route-template normalization, bounded metric labels,
exposition names and the structlog trace/log correlation processor.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from prometheus_client.parser import text_string_to_metric_families

from app.config import Settings
from app.shared.logging import add_trace_context
from app.shared.telemetry import (
    FORBIDDEN_METRIC_LABELS,
    HTTP_IN_FLIGHT_LABELS,
    HTTP_REQUEST_LABELS,
    UNMATCHED_ROUTE,
    WORKER_EVENT_LABELS,
    WORKER_UNIT_LABELS,
    Telemetry,
    configure_telemetry,
    create_telemetry,
    resolve_route_template,
    trace_ids,
)

# Derived from the module's label policy so a change there cannot silently
# drift away from what is recorded and asserted.
_ALL_ALLOWED_LABELS = (
    set(HTTP_REQUEST_LABELS)
    | set(HTTP_IN_FLIGHT_LABELS)
    | set(WORKER_EVENT_LABELS)
    | set(WORKER_UNIT_LABELS)
)


def _settings(*, otlp_endpoint: str | None = None) -> Settings:
    return Settings(otel_exporter_otlp_endpoint=otlp_endpoint)


@pytest.fixture
def telemetry() -> Iterator[Telemetry]:
    stack = create_telemetry(_settings(), span_exporter=InMemorySpanExporter())
    try:
        yield stack
    finally:
        stack.tracer_provider.shutdown()
        stack.meter_provider.shutdown()


def _labels_in(text: str) -> set[str]:
    labels: set[str] = set()
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            # `le` is the histogram bucket boundary added by the exposition
            # format, not a label this code defines.
            labels.update(name for name in sample.labels if name != "le")
    return labels


def test_trace_ids_are_w3c_hex_inside_a_span() -> None:
    telemetry = create_telemetry(_settings(), span_exporter=InMemorySpanExporter())
    tracer = telemetry.tracer_provider.get_tracer("test")

    try:
        assert trace_ids() == (None, None)
        with tracer.start_as_current_span("unit"):
            trace_id, span_id = trace_ids()

        assert trace_id is not None
        assert span_id is not None
        assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
        assert re.fullmatch(r"[0-9a-f]{16}", span_id)
    finally:
        telemetry.tracer_provider.shutdown()
        telemetry.meter_provider.shutdown()


class _MatchedRoute:
    path = "/api/v1/employees/{employee_id}"


def test_route_template_uses_the_matched_template_not_the_raw_path() -> None:
    scope = {
        "route": _MatchedRoute(),
        "path": "/api/v1/employees/0190a3de-6b1f-7c2a-9f00-5f0e7d1a2b3c",
        "raw_path": b"/api/v1/employees/0190a3de?token=secret",
    }

    assert resolve_route_template(scope) == "/api/v1/employees/{employee_id}"


def test_included_router_uses_the_effective_prefixed_template() -> None:
    """FastAPI keeps the router-relative path on `scope["route"]`.

    The effective context carries the include prefix; the metric label must
    use `/api/v1/employees/{employee_id}`, not `/employees/{employee_id}` and
    never the concrete UUID.
    """

    class _RouterRelativeRoute:
        path = "/employees/{id}"

    class _EffectiveRoute:
        path = "/api/v1/employees/{employee_id}"

    scope = {
        "fastapi": {"effective_route_context": _EffectiveRoute()},
        "route": _RouterRelativeRoute(),
        "path": "/api/v1/employees/0190a3de-6b1f-7c2a-9f00-5f0e7d1a2b3c",
    }

    assert resolve_route_template(scope) == "/api/v1/employees/{employee_id}"


def test_unmatched_routes_collapse_to_the_unmatched_label() -> None:
    assert resolve_route_template({}) == UNMATCHED_ROUTE
    assert (
        resolve_route_template(
            {"path": "/api/v1/does-not-exist/12345", "raw_path": b"/x?y=z"}
        )
        == UNMATCHED_ROUTE
    )


def test_http_metrics_use_contract_names_and_bounded_labels(
    telemetry: Telemetry,
) -> None:
    telemetry.http_request_started(method="GET")
    telemetry.record_http_request(
        method="GET",
        route="/api/v1/employees/{employee_id}",
        status_code=200,
        duration_seconds=0.12,
    )
    telemetry.http_request_finished(method="GET")

    text = telemetry.metrics_text().decode()

    assert (
        'workpulse_http_requests_total{method="GET",'
        'route="/api/v1/employees/{employee_id}",status_code="200"} 1.0' in text
    )
    assert "workpulse_http_request_duration_seconds_bucket" in text
    assert "workpulse_http_request_duration_seconds_count" in text
    assert 'workpulse_http_requests_in_flight{method="GET"} 0.0' in text

    labels = _labels_in(text)
    assert labels == set(HTTP_REQUEST_LABELS)
    assert labels.isdisjoint(FORBIDDEN_METRIC_LABELS)


def test_unmatched_paths_are_never_exposed_verbatim(telemetry: Telemetry) -> None:
    route = resolve_route_template({"path": "/api/v1/does-not-exist/12345"})
    telemetry.record_http_request(
        method="GET",
        route=route,
        status_code=404,
        duration_seconds=0.01,
    )

    text = telemetry.metrics_text().decode()

    assert 'route="UNMATCHED"' in text
    assert "/api/v1/does-not-exist/12345" not in text


def test_worker_metrics_use_bounded_labels(telemetry: Telemetry) -> None:
    telemetry.record_worker_unit(
        worker="relay",
        event_type="attendance.checked_in",
        status="succeeded",
        duration_seconds=0.2,
    )
    telemetry.record_worker_unit(
        worker="relay",
        event_type="attendance.checked_in",
        status="failed",
        duration_seconds=0.3,
    )

    text = telemetry.metrics_text().decode()

    assert (
        'workpulse_worker_events_total{event_type="attendance.checked_in",'
        'status="succeeded",worker="relay"} 1.0' in text
    )
    assert (
        'workpulse_worker_events_total{event_type="attendance.checked_in",'
        'status="failed",worker="relay"} 1.0' in text
    )
    assert 'workpulse_worker_unit_duration_seconds_count{status="failed"' in text

    labels = _labels_in(text)
    assert labels == set(WORKER_EVENT_LABELS)
    assert labels.isdisjoint(FORBIDDEN_METRIC_LABELS)


def test_metric_labels_never_include_identifiers_or_pii(
    telemetry: Telemetry,
) -> None:
    """Only the contract's bounded labels ever reach Prometheus."""
    telemetry.record_http_request(
        method="POST",
        route="/api/v1/attendance/check-in",
        status_code=201,
        duration_seconds=0.05,
    )
    telemetry.record_worker_unit(
        worker="notifications",
        event_type="leave.approved",
        status="skipped",
        duration_seconds=0.01,
    )

    labels = _labels_in(telemetry.metrics_text().decode())

    assert labels <= _ALL_ALLOWED_LABELS
    assert labels.isdisjoint(FORBIDDEN_METRIC_LABELS)
    assert "tenant_id" not in labels
    assert "request_id" not in labels
    assert "trace_id" not in labels


def test_trace_context_processor_binds_ids_inside_a_span(telemetry: Telemetry) -> None:
    tracer = telemetry.tracer_provider.get_tracer("test")

    with tracer.start_as_current_span("unit"):
        expected_trace_id, expected_span_id = trace_ids()
        event = add_trace_context(None, "info", {"event": "worked"})

    assert event["trace_id"] == expected_trace_id
    assert event["span_id"] == expected_span_id


def test_trace_context_processor_is_a_noop_outside_a_span() -> None:
    original = {"event": "no span"}
    assert add_trace_context(None, "info", dict(original)) == original


def test_domains_do_not_import_telemetry_libraries() -> None:
    """Architecture rule 3: telemetry lives in `app/shared`, never domains."""
    domains = Path(__file__).resolve().parents[2] / "app" / "domains"
    forbidden = ("opentelemetry", "prometheus_client")
    offenders = [
        str(path.relative_to(domains))
        for path in domains.rglob("*.py")
        if any(
            f"import {library}" in path.read_text()
            or f"from {library}" in path.read_text()
            for library in forbidden
        )
    ]

    assert offenders == []


def test_configure_telemetry_is_idempotent() -> None:
    first = configure_telemetry(_settings())
    second = configure_telemetry(_settings())

    assert first is second
    assert first.otlp_export_enabled is False


def test_otlp_export_is_opt_in() -> None:
    disabled = create_telemetry(_settings())
    enabled = create_telemetry(_settings(otlp_endpoint="http://127.0.0.1:4318"))

    try:
        assert disabled.otlp_export_enabled is False
        assert enabled.otlp_export_enabled is True
    finally:
        disabled.tracer_provider.shutdown()
        disabled.meter_provider.shutdown()
        enabled.tracer_provider.shutdown()
        enabled.meter_provider.shutdown()
