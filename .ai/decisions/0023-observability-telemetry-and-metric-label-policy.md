# 23. Observability: in-process OpenTelemetry, direct Prometheus scrape and bounded metric labels

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

`.ai/features/observability.md` (Phase 6, PO decisions 1–4) closes the gap
between Phase 5's request-scoped `request_id` logging and the product's Phase 6
exit metric: *every request traced, p95 latency and error-rate dashboards live*.
The system now runs a FastAPI monolith plus five asynchronous workers
(`relay`, `realtime`, `notifications`, `payroll`, `reports`) across PostgreSQL,
Redis, Kafka, ClickHouse and MinIO, so operators need one place to see request
latency, error rate, worker throughput and failures.

The constraints the design must satisfy:

- Every HTTP request and every worker batch/message/job is traced and measured,
  with W3C `traceparent` adoption, `X-Trace-Id` on responses and
  `trace_id`/`span_id` correlated into `structlog` next to `request_id`.
- Domain packages never import telemetry libraries (AGENTS.md architecture rule
  3, feature rule 4). Telemetry lives in `app/shared/` and worker loop code.
- No PII and no unbounded labels. Route templates, never raw paths or query
  strings; `tenant_id`, `employee_id`, `user_id`, `request_id` and `event_id`
  are forbidden as Prometheus labels (feature rule 5, `.ai/security.md`).
- A missing/unreachable OTLP collector or inactive scrape must never fail an
  HTTP request or stop a worker loop (feature rule 6).
- `GET /metrics` stays out of the OpenAPI contract so `packages/api-client` and
  `contracts/openapi/openapi.json` do not drift (feature rule 7).
- Prometheus scrape config, Grafana datasource and dashboards are versioned and
  provisioned with zero UI clicks via `make observability-up` (feature rule 8).
- No trace storage backend (Tempo/Jaeger) and no `otel-collector` container in
  Docker Compose; traces are exported only when `OTEL_EXPORTER_OTLP_ENDPOINT`
  is configured (PO decision 2).

Alternatives considered:

- **An `otel-collector` sidecar.** Rejected: an extra process and network hop
  for a single-host dev stack. The OTel SDK can bridge metrics into a
  `prometheus_client` registry in-process and export spans directly over OTLP
  HTTP when a collector exists.
- **Tempo/Jaeger in Compose.** Out of scope per PO decision 2. The dashboards
  carry metrics; traces remain in-process and log-correlated.
- **Instrumenting directly with `prometheus_client` only.** Viable and the
  recorded fallback (see Consequences), but the OTel SDK gives one coherent
  instrument surface for spans and metrics and an OTLP escape hatch, behind a
  small `app/shared/telemetry.py` seam that keeps the fallback local.
- **Propagating `traceparent` through `event_outbox` or Kafka headers.** Out of
  scope: it needs a schema/transport migration for no Phase 6 exit-metric gain.
  Worker spans are root spans per unit.
- **Adding `tenant_id`/`employee_id` metric labels.** Rejected: PII and
  unbounded cardinality. They stay in logs and span attributes only.
- **`X-Trace-Id` generated in middleware only.** Rejected for unhandled 500s:
  Starlette's outermost `ServerErrorMiddleware` renders those outside the user
  middleware stack, so the trace id is also stashed in the request scope and the
  global handler echoes it.

## Decision

1. **One telemetry bootstrap, installed once per process.**
   `app/shared/telemetry.py` owns a `TracerProvider` and `MeterProvider`.
   `configure_telemetry(settings)` is idempotent and sets the OTel global
   providers; the app factory calls it before building FastAPI (so in-process
   clients that skip lifespan still get telemetry), and every worker's `_run()`
   calls it before entering its loop. The app lifespan flushes pending
   spans/metrics on shutdown. Providers are process-wide and are not shut down
   at a lifespan boundary (test/TestClient instances would silently lose
   telemetry).

2. **OTLP HTTP export is opt-in.**
   When `OTEL_EXPORTER_OTLP_ENDPOINT` is empty, no exporter is created and no
   network call is attempted. When set, spans go through a `BatchSpanProcessor`
   to `<endpoint>/v1/traces`; export failures are dropped by the processor and
   never reach the request or worker path. A worker shutdown flushes
   best-effort, and a flush failure only logs a bounded warning.

3. **HTTP instrumentation lives in middleware, not domains.**
   `TelemetryMiddleware` (beside and inside CORS, outside
   `RequestContextMiddleware`) extracts W3C `traceparent`/`tracestate`
   best-effort, starts a `SERVER` span, binds `trace_id`/`span_id` into
   `structlog` contextvars for the request log line, records the bounded HTTP
   counters/histogram/gauge, sets the span name and `http.route` to the matched
   route template, sets `ERROR` status on 5xx without exception messages, and
   always decrements the in-flight gauge in `finally`. Malformed or missing
   `traceparent` headers start a fresh root span and never fail the request.
   `GET /metrics` is excluded from self-instrumentation so scrapes do not
   distort error-rate and latency panels.

4. **Route templates and bounded labels are the only metric dimensions.**
   The resolved label is the FastAPI effective route template (for example
   `/api/v1/employees/{employee_id}`) or `UNMATCHED` for unknown paths — never a
   raw path, UUID or query string. The full label policy:

   | Metric | Type | Labels |
   | --- | --- | --- |
   | `workpulse_http_requests_total` | counter | `method`, `route`, `status_code` |
   | `workpulse_http_request_duration_seconds` | histogram | `method`, `route`, `status_code` |
   | `workpulse_http_requests_in_flight` | up/down counter | `method` |
   | `workpulse_worker_events_total` | counter | `worker`, `event_type`, `status` |
   | `workpulse_worker_unit_duration_seconds` | histogram | `worker`, `status` |

   `event_type` is sanitized to a dotted lowercase identifier or `unknown`, so
   hostile topic text cannot inflate or poison a label. The forbidden-label set
   is declared next to the instruments and asserted by tests.

5. **Metrics exposition: OTel SDK → `prometheus_client` registry → text.**
   `opentelemetry-exporter-prometheus`'s `PrometheusMetricReader` writes into a
   dedicated `CollectorRegistry`; the API serves `generate_latest(registry)` on
   `GET /metrics` as `text/plain; version=0.0.4; charset=utf-8`. Counter
   instruments omit the `_total` suffix internally because the Prometheus client
   appends it at exposition time, matching the contract names exactly. Each
   worker optionally starts `prometheus_client.start_http_server` on
   `127.0.0.1:<worker>_metrics_port`; `0`/unset disables the listener so tests
   and parallel runs never bind a port, and a bind failure is logged and
   swallowed.

6. **Worker units are root spans with the same instrument set.**
   `worker_unit(worker, event_type)` wraps one batch/message/job in a root
   `CONSUMER` span, binds `trace_id`/`span_id` into `structlog` for the unit,
   and records `status="succeeded"|"failed"|"skipped"` plus unit duration. A
   raised exception records its *type* only, re-raises for the existing
   loop/backoff handling, and never kills the worker loop. The per-unit seams
   are relay `_publish_batch` (per row), realtime/notifications/reports
   `_handle` (per message) and payroll `_drain` (per run).

7. **The local stack is code, not clicks.**
   `docker-compose.yml` adds `prometheus` (published on `127.0.0.1:9090`) and
   `grafana` (published on `127.0.0.1:3001`, anonymous Viewer).
   `ops/observability/prometheus/prometheus.yml` scrapes the API and the five
   worker ports through Docker's host gateway. Grafana provisions its
   Prometheus datasource (`http://prometheus:9090`) and the
   `workpulse-overview` dashboard from checked-in JSON: HTTP p95 latency, HTTP
   error rate, request throughput, in-flight requests, worker throughput and
   worker failures. `make observability-up` / `make observability-down` own the
   lifecycle so `make dev` and `make check` stay fast.

8. **`GET /metrics` is operational plumbing, not product API.**
   It is unauthenticated like `GET /healthz`, registered with
   `include_in_schema=False`, and carries aggregate, tenant-agnostic values
   only. `test_openapi_drift.py` and `make gen-api` prove zero contract drift.

## Consequences

- Every request log line inside the middleware carries `trace_id`/`span_id` next
  to `request_id`, and every response carries `X-Trace-Id`; an operator can
  correlate a slow request across logs without a trace store.
- No trace storage backend exists in Compose. Without an
  `OTEL_EXPORTER_OTLP_ENDPOINT`, spans live only in-process; the exit metric is
  served by metrics and log correlation. This is PO decision 2, revisited only
  when a trace backend gets its own spec.
- Worker spans are roots per unit; there is no cross-hop trace continuity from
  an HTTP request through `event_outbox`/Kafka. Adding it needs a transport
  contract change and a new ADR.
- Metric cardinality is bounded by construction: route templates, sanitized
  event types, and a fixed worker/status vocabulary. New instruments must reuse
  this policy; a test asserts domains stay telemetry-free and labels stay inside
  the declared sets.
- Scrape reachability is host-network-dependent. Prometheus runs in the compose
  bridge network and targets `host.docker.internal`; Docker Desktop forwards
  that to host loopback, while a plain Linux Docker engine maps it to the bridge
  gateway, where listeners bound strictly to `127.0.0.1` (the dev API and worker
  metrics servers) are not reachable. `make observability-up` still proves
  Prometheus readiness and dashboard provisioning, and the config test pins the
  ports; live target data on Linux requires the dev listeners to be reachable
  from the container (for example a host-network override). This is a local
  scraping ergonomic, not a request-path or test dependency.
- `opentelemetry-exporter-prometheus` and `prometheus_client` are coupled at the
  registry boundary. If a future exporter release breaks the bridge, the
  fallback is to record the same instruments directly with `prometheus_client`
  behind the `app/shared/telemetry.py` API; the dashboards and label policy do
  not change.
- The pre-existing dev PostgreSQL port drift (`5434` published vs `5432`
  documented) is unrelated to this feature and remains a separate TODO.
