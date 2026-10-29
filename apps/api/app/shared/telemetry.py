"""OpenTelemetry bootstrap, bounded instruments and trace/log correlation.

This module is one of only two places allowed to import `opentelemetry` /
`prometheus_client` (the other is `app/shared/logging.py`); domains stay
telemetry-free (AGENTS.md architecture rule 3, `.ai/features/observability.md`
business rule 4).

Design notes (ADR 0023):

- Spans are always collected in-process. OTLP HTTP export is opt-in: when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is empty no exporter is created and no network
  call is attempted. An unreachable collector only drops spans in the batch
  processor; request handling never waits on it (business rule 6).
- Metrics are recorded with the OpenTelemetry SDK and exposed through
  `opentelemetry-exporter-prometheus` into a dedicated `prometheus_client`
  registry. Counter instruments omit the `_total` suffix because the
  Prometheus client appends it at exposition time, so the exposed names match
  the `## API contract` table exactly.
- Metric labels are deliberately bounded: `method`, `route` (a FastAPI route
  *template*, never a raw path), `status_code`, `worker`, `event_type`,
  `status`. Tenant/employee/user/request/event identifiers never become
  labels (business rule 5).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from prometheus_client import CollectorRegistry, generate_latest

from app.config import Settings, get_settings

# --- Bounded label policy (business rule 5) ---------------------------------

HTTP_REQUEST_LABELS = ("method", "route", "status_code")
HTTP_IN_FLIGHT_LABELS = ("method",)
WORKER_EVENT_LABELS = ("worker", "event_type", "status")
WORKER_UNIT_LABELS = ("worker", "status")

#: Identifiers that must never become Prometheus labels: they are either PII
#: or unbounded (tenant-scoped) values. They remain usable in logs and span
#: attributes.
FORBIDDEN_METRIC_LABELS = frozenset(
    {"tenant_id", "employee_id", "user_id", "request_id", "event_id"}
)

UNMATCHED_ROUTE = "UNMATCHED"

#: Latency buckets in seconds, centred on the 300 ms p95 target.
HTTP_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.3,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

WORKER_DURATION_BUCKETS = (0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0)

# Counter instrument names: the Prometheus client appends `_total` at
# exposition time, so the scraped names are `workpulse_http_requests_total`
# and `workpulse_worker_events_total` as specified in the feature contract.
_HTTP_REQUESTS = "workpulse_http_requests"
_HTTP_DURATION = "workpulse_http_request_duration_seconds"
_HTTP_IN_FLIGHT = "workpulse_http_requests_in_flight"
_WORKER_EVENTS = "workpulse_worker_events"
_WORKER_DURATION = "workpulse_worker_unit_duration_seconds"


def trace_ids() -> tuple[str | None, str | None]:
    """Return the active span's W3C ids as `(trace_id, span_id)`.

    `(32-hex, 16-hex)` when a valid span is active, `(None, None)` otherwise —
    callers (the structlog processor) omit the fields rather than inventing
    values.
    """
    span_context = otel_trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None
    return (
        format(span_context.trace_id, "032x"),
        format(span_context.span_id, "016x"),
    )


def resolve_route_template(scope: Mapping[str, Any]) -> str:
    """Return the matched FastAPI route template, or `UNMATCHED`.

    `scope["route"]` is the matched `APIRoute` (with an `APIRoute.path`
    template such as `/api/v1/employees/{employee_id}`); unmatched requests
    have no route. Raw URL paths and query strings never reach a metric label,
    so arbitrary/scanner traffic cannot blow up label cardinality.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Configured providers and the bounded instrument set."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    registry: CollectorRegistry
    http_requests: Counter
    http_duration: Histogram
    http_in_flight: UpDownCounter
    worker_events: Counter
    worker_duration: Histogram
    otlp_export_enabled: bool

    def record_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        attributes = {
            "method": method,
            "route": route,
            "status_code": str(status_code),
        }
        self.http_requests.add(1, attributes)
        self.http_duration.record(duration_seconds, attributes)

    def http_request_started(self, *, method: str) -> None:
        self.http_in_flight.add(1, {"method": method})

    def http_request_finished(self, *, method: str) -> None:
        self.http_in_flight.add(-1, {"method": method})

    def record_worker_unit(
        self,
        *,
        worker: str,
        event_type: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        self.worker_events.add(
            1,
            {"worker": worker, "event_type": event_type, "status": status},
        )
        self.worker_duration.record(
            duration_seconds,
            {"worker": worker, "status": status},
        )

    def metrics_text(self) -> bytes:
        """Prometheus text exposition (`text/plain; version=0.0.4`)."""
        return generate_latest(self.registry)

    def flush(self) -> None:
        """Flush pending spans/metrics without tearing providers down.

        The global OTel providers are process-wide and set once; shutting them
        down at an app lifespan boundary would silently disable telemetry for
        a later app instance in the same process (tests, TestClient). Flushing
        is the useful part of shutdown for the OTLP batch processor.
        """
        self.tracer_provider.force_flush()
        self.meter_provider.force_flush()


def _resolve_otlp_endpoint(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if value.endswith("/v1/traces"):
        return value
    return f"{value.rstrip('/')}/v1/traces"


def create_telemetry(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    registry: CollectorRegistry | None = None,
) -> Telemetry:
    """Build a self-contained telemetry stack (no global provider changes).

    Tests pass an `InMemorySpanExporter` and an isolated registry; the app
    wires the result into the process-wide providers with
    `configure_telemetry`.
    """
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.version,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    endpoint = _resolve_otlp_endpoint(settings.otel_exporter_otlp_endpoint)
    otlp_export_enabled = endpoint is not None
    exporter = span_exporter
    if exporter is None and endpoint is not None:
        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=10)
    if exporter is not None:
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    collector_registry = registry if registry is not None else CollectorRegistry()
    metric_reader = PrometheusMetricReader(
        registry=collector_registry,
        disable_target_info=True,
        scope_info_enabled=False,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    meter: Meter = meter_provider.get_meter("workpulse")

    http_requests = meter.create_counter(
        _HTTP_REQUESTS,
        unit="1",
        description="Total completed HTTP requests.",
    )
    http_duration = meter.create_histogram(
        _HTTP_DURATION,
        unit="s",
        description="HTTP request latency in seconds.",
        explicit_bucket_boundaries_advisory=HTTP_DURATION_BUCKETS,
    )
    http_in_flight = meter.create_up_down_counter(
        _HTTP_IN_FLIGHT,
        unit="1",
        description="Currently executing HTTP requests.",
    )
    worker_events = meter.create_counter(
        _WORKER_EVENTS,
        unit="1",
        description="Worker units processed, by outcome.",
    )
    worker_duration = meter.create_histogram(
        _WORKER_DURATION,
        unit="s",
        description="Duration of one worker batch/message/job in seconds.",
        explicit_bucket_boundaries_advisory=WORKER_DURATION_BUCKETS,
    )

    return Telemetry(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        registry=collector_registry,
        http_requests=http_requests,
        http_duration=http_duration,
        http_in_flight=http_in_flight,
        worker_events=worker_events,
        worker_duration=worker_duration,
        otlp_export_enabled=otlp_export_enabled,
    )


_LOCK = threading.Lock()
_TELEMETRY: Telemetry | None = None


def configure_telemetry(settings: Settings | None = None) -> Telemetry:
    """Install the process-wide providers once and return the telemetry bundle.

    Idempotent: repeated calls (app factory, lifespan, tests) reuse the first
    bundle so instruments and the Prometheus registry are never duplicated.
    """
    global _TELEMETRY

    with _LOCK:
        if _TELEMETRY is None:
            resolved = settings if settings is not None else get_settings()
            telemetry = create_telemetry(resolved)
            otel_trace.set_tracer_provider(telemetry.tracer_provider)
            otel_metrics.set_meter_provider(telemetry.meter_provider)
            _TELEMETRY = telemetry
        return _TELEMETRY


def get_telemetry() -> Telemetry | None:
    """The configured bundle, or `None` before `configure_telemetry`."""
    return _TELEMETRY
