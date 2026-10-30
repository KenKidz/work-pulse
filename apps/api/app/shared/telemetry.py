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
- Each background worker wraps one batch/message/job in `worker_unit`: a
  root span with the same bounded instruments, plus a loopback-only
  `prometheus_client` listener (`start_worker_metrics_server`) when its
  configured port is non-zero.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import structlog
from opentelemetry import context as otel_context
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
from opentelemetry.propagate import extract
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import CollectorRegistry, generate_latest, start_http_server
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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

#: Operational scrape endpoint on the API. Excluded from HTTP metrics so
#: Prometheus scrapes never distort the application error-rate panels.
METRICS_PATH = "/metrics"

#: Scope key carrying the request's formatted W3C trace id. Starlette's
#: `ServerErrorMiddleware` renders unhandled 500s outside the user middleware
#: stack, so the global 500 handler reads the id from here to keep the
#: response correlatable (ADR 0023).
TRACE_ID_SCOPE_KEY = "workpulse.trace_id"

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
_CDC_DROPPED_DELETES = "workpulse_cdc_dropped_deletes"
_CDC_SINK_ERRORS = "workpulse_cdc_sink_errors"
_CDC_REFERENCE_MISSES = "workpulse_cdc_reference_misses"


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

    Routes mounted through `include_router` keep their router-relative
    `path` (e.g. `/employees/{id}`) on `scope["route"]`; FastAPI publishes
    the *effective* template (prefix included) under its scope namespace.
    Fall back to `scope["route"].path` for top-level routes, and to
    `UNMATCHED` when nothing matched. Raw URL paths and query strings never
    reach a metric label, so arbitrary/scanner traffic cannot blow up label
    cardinality.
    """
    fastapi_scope = scope.get("fastapi")
    if isinstance(fastapi_scope, Mapping):
        context = fastapi_scope.get("effective_route_context")
        path = getattr(context, "path", None)
        if isinstance(path, str) and path:
            return path

    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


#: Event-type label shape: contract events are dotted lowercase identifiers
#: (`attendance.checked_in`). Anything else — hostile topic text, PII, an
#: over-long value — collapses to `unknown` so it cannot inflate or poison a
#: metric label (business rule 5).
_EVENT_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


def event_type_label(value: object) -> str:
    """A bounded, PII-safe `event_type` label for spans and worker metrics."""
    if (
        isinstance(value, str)
        and len(value) <= 64
        and _EVENT_TYPE_PATTERN.fullmatch(value) is not None
    ):
        return value
    return "unknown"


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
    cdc_dropped_deletes: Counter
    cdc_sink_errors: Counter
    cdc_reference_misses: Counter
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

    def record_cdc_dropped_delete(self) -> None:
        """Count a dropped CDC delete/truncate op (no projection row removed)."""
        self.cdc_dropped_deletes.add(1)

    def record_cdc_sink_error(self) -> None:
        """Count a transient CDC sink failure (store/commit retry)."""
        self.cdc_sink_errors.add(1)

    def record_cdc_reference_miss(self) -> None:
        """Count a leave-type reference miss that fell back to `unknown`."""
        self.cdc_reference_misses.add(1)

    def metrics_text(self) -> bytes:
        """Prometheus text exposition (`text/plain; version=0.0.4`)."""
        return generate_latest(self.registry)

    def flush(self, timeout_millis: int = 5000) -> None:
        """Flush pending spans/metrics without tearing providers down.

        The global OTel providers are process-wide and set once; shutting them
        down at an app lifespan boundary would silently disable telemetry for
        a later app instance in the same process (tests, TestClient). Flushing
        is the useful part of shutdown for the OTLP batch processor.

        Callers must offload this to a worker thread: the SDK's force-flush is
        a blocking export and the timeout is advisory (an unreachable collector
        can still hold a batch for the exporter's own timeout).
        """
        self.tracer_provider.force_flush(timeout_millis=timeout_millis)
        self.meter_provider.force_flush(timeout_millis=timeout_millis)


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
    cdc_dropped_deletes = meter.create_counter(
        _CDC_DROPPED_DELETES,
        unit="1",
        description="CDC delete/truncate changes dropped without deleting a row.",
    )
    cdc_sink_errors = meter.create_counter(
        _CDC_SINK_ERRORS,
        unit="1",
        description="Transient CDC sink failures retried in place.",
    )
    cdc_reference_misses = meter.create_counter(
        _CDC_REFERENCE_MISSES,
        unit="1",
        description="CDC leave-type reference misses resolved to unknown.",
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
        cdc_dropped_deletes=cdc_dropped_deletes,
        cdc_sink_errors=cdc_sink_errors,
        cdc_reference_misses=cdc_reference_misses,
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


def flush_worker_telemetry() -> None:
    """Best-effort span/metric flush for a worker shutdown (never raises).

    The worker's `*_stopped` log line is load-bearing for dev teardown; a
    metric-reader or exporter hiccup must never suppress it (business rule 6).
    """
    telemetry = get_telemetry()
    if telemetry is None:
        return
    try:
        telemetry.flush()
    except Exception as exc:
        structlog.get_logger("workpulse.telemetry").warning(
            "telemetry_flush_failed",
            error=type(exc).__name__,
        )


def record_cdc_dropped_delete() -> None:
    """Count a dropped CDC delete/truncate op (inert without telemetry)."""
    telemetry = get_telemetry()
    if telemetry is not None:
        telemetry.record_cdc_dropped_delete()


def record_cdc_sink_error() -> None:
    """Count a transient CDC sink failure (inert without telemetry)."""
    telemetry = get_telemetry()
    if telemetry is not None:
        telemetry.record_cdc_sink_error()


def record_cdc_reference_miss() -> None:
    """Count a leave-type reference miss (inert without telemetry)."""
    telemetry = get_telemetry()
    if telemetry is not None:
        telemetry.record_cdc_reference_miss()


@dataclass(slots=True)
class WorkerUnit:
    """Mutable outcome of one worker batch/message/job.

    Callers that swallow an error inside the unit record it with
    `mark_failed`; deliberate skips call `mark_skipped`. The context manager
    records the final status and duration exactly once, in `finally`.
    """

    worker: str
    event_type: str
    status: str = "succeeded"
    span: otel_trace.Span | None = None

    def mark_skipped(self) -> None:
        """Record a deliberate, terminal skip (never a failure)."""
        if self.status == "succeeded":
            self.status = "skipped"

    def mark_failed(self, error_type: str) -> None:
        """Record a handled failure without ever logging its message (rule 5)."""
        self.status = "failed"
        if self.span is not None:
            self.span.set_attribute("error.type", error_type)
            self.span.set_status(Status(StatusCode.ERROR))


@asynccontextmanager
async def worker_unit(worker: str, event_type: str) -> AsyncIterator[WorkerUnit]:
    """Wrap one unit of worker work in a root span plus bounded metrics.

    Every worker batch/message/job gets its own root span (worker spans are
    not linked across the bus — no `eventparent`/Kafka header propagation in
    scope) and the active span feeds `structlog`'s `trace_id`/`span_id`
    processor for the duration of the unit. Success, failure and skip all
    record `workpulse_worker_events_total` and
    `workpulse_worker_unit_duration_seconds` with bounded labels only.

    Without a configured telemetry stack (unit tests) the helper is inert:
    worker loops keep running exactly as before (business rule 6).
    """
    telemetry = get_telemetry()
    unit = WorkerUnit(worker=worker, event_type=event_type)
    if telemetry is None:
        yield unit
        return

    tracer = telemetry.tracer_provider.get_tracer("workpulse.worker")
    span = tracer.start_span(
        f"{worker} {event_type}",
        context=otel_context.Context(),
        kind=SpanKind.CONSUMER,
    )
    span.set_attribute("workpulse.worker", worker)
    span.set_attribute("workpulse.event_type", event_type)
    unit.span = span
    token = otel_context.attach(otel_trace.set_span_in_context(span))
    started = time.perf_counter()
    try:
        yield unit
    except BaseException as exc:
        # Re-raise for the worker's existing loop/backoff handling; only the
        # exception type reaches the span, never its message (rule 5).
        unit.mark_failed(type(exc).__name__)
        raise
    finally:
        span.set_attribute("workpulse.status", unit.status)
        telemetry.record_worker_unit(
            worker=worker,
            event_type=event_type,
            status=unit.status,
            duration_seconds=time.perf_counter() - started,
        )
        otel_context.detach(token)
        span.end()


@dataclass(frozen=True, slots=True)
class WorkerMetricsServer:
    """A loopback-only Prometheus exposition listener for one worker process."""

    server: Any
    thread: threading.Thread

    def stop(self) -> None:
        """Stop serving and release the port."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_worker_metrics_server(port: int) -> WorkerMetricsServer | None:
    """Expose the process registry on `127.0.0.1:<port>` when configured.

    `port <= 0` disables the listener so tests and parallel runs never bind.
    A bind failure is logged and swallowed: scraping is optional and must
    never take a worker down (business rule 6).
    """
    if port <= 0:
        return None
    telemetry = get_telemetry()
    if telemetry is None:
        return None

    try:
        server, thread = start_http_server(
            port,
            addr="127.0.0.1",
            registry=telemetry.registry,
        )
    except OSError as exc:
        structlog.get_logger("workpulse.telemetry").warning(
            "worker_metrics_server_failed",
            port=port,
            error=type(exc).__name__,
        )
        return None
    return WorkerMetricsServer(server=server, thread=thread)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Trace every HTTP request and record the bounded HTTP metrics.

    Placed inside CORS and outside `RequestContextMiddleware` (see
    `app.main.create_app`) so the request log line runs while the server span
    is active and picks up `trace_id`/`span_id` (business rule 1). Incoming
    W3C `traceparent`/`tracestate` headers are extracted best-effort: malformed
    values start a fresh root span instead of failing the request (rule 2).

    An unconfigured telemetry stack passes traffic straight through — spans,
    metrics and log correlation can never break the write or read path
    (rule 6).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        telemetry = get_telemetry()
        if telemetry is None:
            return await call_next(request)

        parent_context = extract(carrier=dict(request.headers))
        tracer = telemetry.tracer_provider.get_tracer("workpulse.http")
        # Start with a bounded name; the route template is set once in `finally`
        # so every termination path (including cancellation) exports a template
        # and no `on_start` processor sees a raw path.
        span = tracer.start_span(
            request.method,
            context=parent_context,
            kind=SpanKind.SERVER,
        )
        span.set_attribute("http.request.method", request.method)
        trace_id = format(span.get_span_context().trace_id, "032x")
        token = otel_context.attach(
            otel_trace.set_span_in_context(span, parent_context)
        )

        method = request.method
        is_scrape = request.url.path == METRICS_PATH
        started = time.perf_counter()
        status_code = 500
        in_flight_started = False

        try:
            if not is_scrape:
                telemetry.http_request_started(method=method)
                in_flight_started = True
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Trace-Id"] = trace_id
            return response
        except Exception as exc:
            # Exception type only, never its message (rule 5); the handler
            # above still receives the original exception.
            span.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            route = resolve_route_template(request.scope)
            span.update_name(f"{method} {route}")
            span.set_attribute("http.route", route)
            span.set_attribute("http.response.status_code", status_code)
            if status_code >= 500:
                # Status only: exception messages may carry values (rule 5).
                span.set_status(Status(StatusCode.ERROR))

            # The outermost 500 handler runs outside this middleware; hand it
            # the trace id so unhandled errors stay correlatable (decisions).
            request.scope[TRACE_ID_SCOPE_KEY] = trace_id

            if not is_scrape:
                telemetry.record_http_request(
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_seconds=time.perf_counter() - started,
                )
                # Always decremented once incremented — even on cancellation
                # or exceptions — so the in-flight gauge can never leak
                # (edge cases).
                if in_flight_started:
                    telemetry.http_request_finished(method=method)
            otel_context.detach(token)
            span.end()
