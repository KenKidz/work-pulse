# Feature: Observability (OpenTelemetry, Prometheus, Grafana)

- **Status:** done
- **Phase:** 6
- **Size:** M
- **Surfaces:** api+web+events+workers (web and event contracts untouched — the
  template ladder's only value covering `api` + `workers`; real work is
  OpenTelemetry tracing/metrics/log correlation across `apps/api` and
  `workers/*`, plus provisioned Prometheus and Grafana in `docker-compose.yml`)
- **Links:** `.ai/product.md` (Success metrics row 6: "Every request traced; p95
  latency + error rate dashboards live") · `.ai/architecture.md` ("Later
  infrastructure" + Phase roadmap row 6: `OpenTelemetry ──► Prometheus ──►
  Grafana`) · `.ai/conventions.md` (`structlog` logging, `/healthz` rule) ·
  `.ai/security.md` ("Never log PII" — applies equally to spans, metrics and
  logs) · ADR 0006 (Phase 6 OpenTelemetry SDK + instrumentations; structlog
  trace/log correlation) · ADR 0012 (`workers/relay`) · ADR 0013
  (`workers/realtime`) · ADR 0015 (`workers/notifications`) · ADR 0019
  (`workers/reports`)

## Problem

WorkPulse now runs a modular FastAPI monolith alongside five asynchronous
workers (`relay`, `realtime`, `notifications`, `payroll`, `reports`) across
PostgreSQL, Redis, Kafka, ClickHouse and MinIO — yet visibility into runtime
behavior stops at per-process `structlog` lines with an HTTP-only `request_id`.
When a check-in or report query slows down, or when a worker stalls against a
downstream store, operators cannot measure p95 latency, HTTP error rate, or
worker batch/event throughput without grepping container logs, and cannot
correlate an HTTP request's logs with its database or cache spans. Phase 6's
exit metric requires every request to be traced with live p95 latency and error
rate dashboards via `OpenTelemetry ──► Prometheus ──► Grafana`.

## Business rules

1. **Every HTTP request is traced and measured.** Every request handled by
   `apps/api` runs inside an OpenTelemetry server span, records HTTP request
   count, in-flight gauge and duration histogram metrics, binds `trace_id` and
   `span_id` (32-hex and 16-hex W3C identifiers) into `structlog` contextvars
   alongside `request_id`, and returns `X-Trace-Id` alongside `X-Request-Id` on
   the HTTP response.
2. **W3C Trace Context propagation.** Incoming `traceparent` (and `tracestate`)
   headers are extracted so callers can continue a distributed trace; malformed
   or missing `traceparent` headers never fail the request — the tracer starts a
   fresh root span instead.
3. **Every worker iteration/message is traced and measured.** Each background
   worker (`relay`, `realtime`, `notifications`, `payroll`, `reports`) wraps its
   unit of work (batch poll or consumed event/job) in an OpenTelemetry root span
   with `trace_id`/`span_id` bound into `structlog` for the duration of that
   unit, and increments bounded worker counters/histograms (processed, failed,
   duration). Each worker optionally starts a loopback-only Prometheus HTTP
   exporter on its configured port (`:9101`–`:9105` in dev; `0`/disabled by
   default in tests).
4. **Domain purity holds (architecture rule 3).** Domain packages under
   `apps/api/app/domains/<domain>/` never import `opentelemetry` or
   `prometheus_client`. Telemetry lives in `app/shared/telemetry.py` (and
   `app/shared/logging.py`), FastAPI middleware/lifespan, infrastructure
   adapters and `workers/*/main.py` / runner loops.
5. **Zero PII and bounded metric cardinality (`.ai/security.md`).**
   - Span attributes, metric labels and correlated logs must never contain PII
     (names, emails, phones, salaries, leave/overtime reasons, document titles
     or object keys, session tokens, SQL bind parameter values, or raw request/
     response bodies).
   - Prometheus metric labels must use **route templates** (e.g.
     `/api/v1/employees/{employee_id}`, and `UNMATCHED` for unknown 404 paths),
     never raw URL paths containing UUIDs or query strings.
   - High-cardinality identifiers (`tenant_id`, `employee_id`, `user_id`,
     `request_id`, `event_id`) are **forbidden as Prometheus metric labels**;
     safe non-PII identifiers (`request_id`, `event_id`) stay in logs and span
     attributes only.
6. **Observability never breaks the write or read path.** If an OTLP trace
   exporter endpoint is unset or unreachable, or if metric scraping is not
   active, API requests and worker loops continue normally without raising or
   blocking user traffic.
7. **No OpenAPI or `@workpulse/api-client` drift.** The Prometheus scrape
   endpoint `GET /metrics` is operational plumbing registered with
   `include_in_schema=False`, so `contracts/openapi/openapi.json` and
   `packages/api-client` remain unchanged (`make gen-api` produces no diff).
8. **Dashboards and scrape configs are code.** Prometheus scrape configuration
   and Grafana datasource + dashboard definitions live in version control and
   boot ready-to-query via `make observability-up` with zero manual UI clicks.

## Scope

- OpenTelemetry SDK & instrumentation packages in `apps/api/pyproject.toml`
  (ADR 0006 Phase 6 gate): `opentelemetry-api`, `opentelemetry-sdk`,
  `opentelemetry-exporter-prometheus`, `opentelemetry-exporter-otlp-proto-http`
  (optional export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set), and
  `prometheus-client`.
- Shared telemetry bootstrap in `apps/api/app/shared/telemetry.py` + integration
  with `apps/api/app/shared/logging.py` (`trace_id` / `span_id` processor,
  `X-Trace-Id` response header, W3C `traceparent` extraction, route-template
  HTTP metrics middleware).
- `GET /metrics` endpoint on `apps/api` (`include_in_schema=False`) serving
  Prometheus text exposition format (`text/plain; version=0.0.4`).
- Worker telemetry instrumentation across `workers/relay`, `workers/realtime`,
  `workers/notifications`, `workers/payroll`, and `workers/reports` (per-batch /
  per-message spans, structured log correlation, and Prometheus metrics
  exposition when configured).
- Provisioned local observability stack in `docker-compose.yml` (`prometheus`
  and `grafana` services bound to `127.0.0.1`), Prometheus config + Grafana
  datasource and dashboard JSON (p95 HTTP latency, HTTP error rate, request
  throughput, in-flight requests, and worker throughput/failures), and
  `Makefile` targets (`make observability-up` / `make observability-down`).
- Unit, integration and contract tests proving span creation, W3C propagation,
  `structlog` correlation, route-template cardinality normalization, PII/label
  safety, and dashboard/config validity.
- ADR 0023 recording the Phase 6 telemetry architecture, metric label policy and
  exposition model.

## Out of scope

- Web browser Real User Monitoring (RUM) or client-side OpenTelemetry SDK in
  `apps/web`.
- Distributed trace storage backend in Docker Compose (e.g. Jaeger, Tempo,
  ClickHouse trace sink); `docker-compose.yml` adds Prometheus and Grafana only.
- Cross-hop W3C `traceparent` propagation through `event_outbox` or Kafka
  message headers (worker spans are root spans per batch/message; no
  `event_outbox` schema migration).
- Alertmanager routing rules, PagerDuty/Slack alert webhooks, or SLO burn-rate
  paging (Phase 7/8 operational concern).
- Kubernetes ServiceMonitors / Helm charts (Phase 8).
- Modifying domain event schemas under `contracts/events/`.
- Product UI pages in `apps/web` (operators use the provisioned Grafana UI).

## Surfaces & files

| Surface | Location |
| --- | --- |
| Shared telemetry & logging | `apps/api/app/shared/telemetry.py` · `apps/api/app/shared/logging.py` · `apps/api/app/config.py` · `apps/api/app/main.py` |
| API domain | — (domains stay OTel-free per rule 4) |
| API router | `apps/api/app/main.py` (`GET /metrics`, `include_in_schema=False`) |
| Events | — (no event schema changes) |
| Workers | `workers/relay/` · `workers/realtime/` · `workers/notifications/` · `workers/payroll/` · `workers/reports/` |
| Web feature / routes | — (none; Grafana serves dashboards on `127.0.0.1:3001`) |
| Observability config | `ops/observability/prometheus/prometheus.yml` · `ops/observability/grafana/provisioning/` · `ops/observability/grafana/dashboards/workpulse-overview.json` |
| API tests | `apps/api/tests/unit/test_telemetry.py` · `apps/api/tests/integration/test_observability.py` · `apps/api/tests/contract/test_openapi_drift.py` |
| E2E | `tests/e2e/` — regression stays green |
| Infra | `docker-compose.yml` (`prometheus`, `grafana`) · `Makefile` (`observability-up`, `observability-down`) · `.env.example` |

## Roles & access

- No product RBAC matrix rows in `.ai/security.md` change.
- `GET /metrics`: unauthenticated operational endpoint (like `GET /healthz`)
  intended for loopback/internal Prometheus scraping; excluded from OpenAPI
  (`include_in_schema=False`) and carries aggregate, tenant-agnostic counters,
  gauges and histograms only — never tenant-scoped rows or PII.
- Local Prometheus (`127.0.0.1:9090`), Grafana (`127.0.0.1:3001`), and worker
  metric listeners (`127.0.0.1:9101`–`9105`) bind to host loopback only,
  matching Redis, Kafka, ClickHouse and MinIO.

## API contract

### Operational endpoint (`apps/api`)

| Method | Path | Auth | Schema inclusion | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/metrics` | None (loopback/ops scrape) | `include_in_schema=False` (excluded from `contracts/openapi/openapi.json`) | `200 OK`, `Content-Type: text/plain; version=0.0.4; charset=utf-8` (Prometheus exposition format) |

### Response headers on all HTTP routes

| Header | Format | Meaning |
| --- | --- | --- |
| `X-Request-Id` | UUID string (existing) | Per-request correlation id (echoes valid incoming `X-Request-Id` or generates a new UUID) |
| `X-Trace-Id` | 32-char lowercase hex | Active OpenTelemetry W3C trace id for the request |

### Exported Prometheus metrics

| Metric name | Type | Labels (bounded) | Description |
| --- | --- | --- | --- |
| `workpulse_http_requests_total` | Counter | `method`, `route`, `status_code` | Total completed HTTP requests (`route` is the matched FastAPI route template, e.g. `/api/v1/employees/{employee_id}`, or `UNMATCHED`) |
| `workpulse_http_request_duration_seconds` | Histogram | `method`, `route`, `status_code` | HTTP request latency in seconds (buckets suitable for p50/p95/p99 around the 300ms target) |
| `workpulse_http_requests_in_flight` | UpDownCounter / Gauge | `method` | Currently executing HTTP requests |
| `workpulse_worker_events_total` | Counter | `worker`, `event_type`, `status` | Worker units processed (`status`: `succeeded` \| `failed` \| `skipped`; `worker`: `relay` \| `realtime` \| `notifications` \| `payroll` \| `reports`) |
| `workpulse_worker_unit_duration_seconds` | Histogram | `worker`, `status` | Duration of one worker batch/message/job in seconds |

## Events

None added or changed.

## Edge cases

- **Unmatched 404 URLs with arbitrary path segments** (e.g.
  `GET /api/v1/does-not-exist/12345` or scanner traffic): must collapse to
  `route="UNMATCHED"` so attacker-controlled paths cannot cause a Prometheus
  label-cardinality explosion.
- **Parameterized routes** (e.g. `GET /api/v1/employees/019...`): must record
  the route template `/api/v1/employees/{employee_id}`, never the concrete UUID.
- **Malformed incoming `traceparent` header** (e.g. `traceparent: not-a-trace`
  or all-zero trace id): ignored cleanly; request succeeds with a newly
  generated root `trace_id` and `X-Trace-Id` header.
- **Unhandled exception or `WorkPulseError` inside a route:** span status records
  the error (for `5xx`) without embedding PII in the span description, HTTP
  metrics still record the final `status_code` and duration, and `in_flight`
  decrements in a `finally` block so gauges never leak.
- **OTLP exporter disabled or collector unreachable:** default dev/test runs
  have `OTEL_EXPORTER_OTLP_ENDPOINT` unset (no network export attempted); when
  set to an unreachable endpoint, export failure is logged/dropped by the batch
  processor without failing the HTTP request or worker loop.
- **Scrape of `/metrics` itself and `/healthz`:** `/metrics` is excluded from
  self-instrumentation (or labeled `/metrics` consistently) so scraping does not
  distort application error-rate panels.

## Acceptance criteria

- [x] API (tracing + headers): given any HTTP request to `apps/api`, when it
      completes, then an OpenTelemetry server span is recorded, the response
      carries a 32-hex `X-Trace-Id` header, and `structlog` output for the
      request includes matching `trace_id` and `span_id` fields alongside
      `request_id`.
- [x] API (W3C `traceparent` propagation): given a request with a valid W3C
      `traceparent` header (`00-<trace_id>-<parent_span_id>-01`), when `apps/api`
      handles it, then the server span adopts `<trace_id>` as its trace id and
      echoes it in `X-Trace-Id`.
- [x] API (failure — malformed `traceparent`): given a request with an invalid
      `traceparent: garbage` header, when `apps/api` handles it, then the
      request is not rejected and a fresh valid 32-hex `X-Trace-Id` is generated
      and returned.
- [x] API (metrics + route-template cardinality): given requests to a
      parameterized route (`/api/v1/employees/{employee_id}`) and an unknown path
      (`/api/v1/unknown-random-slug`), when `GET /metrics` is scraped, then
      `workpulse_http_requests_total` and
      `workpulse_http_request_duration_seconds` expose `route="/api/v1/employees/{employee_id}"`
      and `route="UNMATCHED"` — never raw UUIDs or raw unmatched paths.
- [x] API (PII & tenant label isolation): given authenticated requests and
      domain errors across tenants, when spans, logs and `GET /metrics` output
      are inspected, then no PII values and no `tenant_id` / `employee_id` /
      `user_id` / `request_id` appear in Prometheus metric labels, and cross-tenant
      reads still return `404` with no tenant leakage on `/metrics`.
- [x] Contract (no OpenAPI drift): given `GET /metrics` registered on the
      FastAPI app, when `test_openapi_drift.py` and `make gen-api` run, then
      `contracts/openapi/openapi.json` and `packages/api-client` have zero diff.
- [x] Workers (tracing + metrics + failure recording): given a worker unit in
      `relay`, `realtime`, `notifications`, `payroll`, and `reports`, when a
      batch/event succeeds or fails, then a worker span is recorded with
      `trace_id`/`span_id` bound in `structlog`, and `workpulse_worker_events_total`
      + `workpulse_worker_unit_duration_seconds` increment with `status="succeeded"`
      or `status="failed"` without killing the worker loop.
- [x] Provisioned Prometheus & Grafana (config + dashboard contract): given the
      checked-in Prometheus configuration and Grafana provisioning/dashboard
      JSON, when validated, then Prometheus targets match the configured ports
      and the Grafana dashboard defines valid panels for HTTP p95 latency, HTTP
      error rate, request throughput, and worker health.

## Decisions (PO, 2026-10-29)

1. **Worker metrics exposition — direct Prometheus scrape:** `apps/api` serves
   `GET /metrics` on `:8000`, and each of the 5 background workers (`relay`,
   `realtime`, `notifications`, `payroll`, `reports`) exposes a loopback-only
   Prometheus HTTP metric endpoint on a dedicated configurable port
   (`:9101`–`:9105` in dev; `0`/disabled by default in tests so parallel runs
   never collide). Prometheus scrapes the API and workers directly with no
   intermediate `otel-collector` container.
2. **Trace backend & propagation — in-process OTel + OTLP opt-in:** Docker
   Compose runs **Prometheus + Grafana** only (no Tempo/Jaeger service).
   OpenTelemetry traces every HTTP request and worker unit in-process, injects
   `trace_id` and `span_id` into `structlog` contextvars, returns `X-Trace-Id`,
   and exports spans over OTLP HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is
   configured. Worker spans are root spans per batch/message (no `event_outbox`
   migration or Kafka header change).
3. **`GET /metrics` access & schema visibility — unauthenticated +
   schema-hidden:** `GET /metrics` on `apps/api` is unauthenticated (like
   `GET /healthz`), registered with `include_in_schema=False` so
   `contracts/openapi/openapi.json` and `packages/api-client` have zero diff,
   and restricted to tenant-agnostic, PII-free, low-cardinality metrics.
4. **Compose lifecycle & ports — `make observability-up` on `:9090` / `:3001`:**
   Prometheus binds to `127.0.0.1:9090` and Grafana binds to `127.0.0.1:3001`
   (avoiding Next.js on `:3000`, with anonymous Viewer enabled for local dev),
   managed via dedicated `make observability-up` / `make observability-down`
   targets so `make dev` and `make check` stay fast.

## Plan

Repo research (read-only) confirmed the spec's assumptions: workers share
`apps/api/pyproject.toml` (single uv env, `pythonpath` reaches `../..`), all
five workers use `app.shared.logging` and expose a per-unit seam (relay
per-row in `_publish_batch`; realtime/notifications/reports per-message in
`_handle`; payroll per-run in `_drain`), `X-Request-Id` lives only in
`RequestContextMiddleware` (`app/shared/logging.py:53–83`), `main.py` owns
middleware order + `/healthz`, `tests/contract/test_openapi_drift.py` already
asserts `app.openapi() == contracts/openapi/openapi.json`, and `ops/` does not
exist yet. No frontend and no event contracts are touched, so the "pin the
contract first" rule is already satisfied by the `## API contract` section
above; `test_openapi_drift.py` keeps the surfaces from drifting (rule 7).

### Acceptance criteria → change map

| AC | Satisfied by |
| --- | --- |
| API tracing + `X-Trace-Id` + structlog correlation | T2 (telemetry bootstrap + structlog `trace_id`/`span_id` processor), T3 (server span + header + middleware) |
| W3C `traceparent` propagation | T3 (context extraction in middleware; OTel SDK handles valid headers) |
| Malformed `traceparent` failure case | T3 (extraction is best-effort; fresh root span) + test in T3 |
| Route-template metrics cardinality | T2 (route-template resolver incl. `UNMATCHED`) + T3 (metrics middleware) + test in T3 |
| PII & tenant label isolation | T2/T3/T4 label policy (only `method`, `route`, `status_code`, `worker`, `event_type`, `status`); test in T3 |
| No OpenAPI drift | T3 registers `GET /metrics` with `include_in_schema=False`; existing `test_openapi_drift.py` + `make gen-api` gate |
| Worker tracing + metrics + failure recording | T4 (per-unit root spans + counters/histograms in all 5 workers) |
| Provisioned Prometheus & Grafana | T5 (`ops/observability/`, `docker-compose.yml`, `make observability-up`) + config/dashboard validity test in T5 |

### Ordered tasks

Each task keeps `make check` green on its own; tests ship with the code they
cover.

**T1 — Dependencies and telemetry settings.**
- `apps/api/pyproject.toml`: add runtime deps `opentelemetry-api`,
  `opentelemetry-sdk`, `opentelemetry-exporter-prometheus`,
  `opentelemetry-exporter-otlp-proto-http`, `prometheus-client` (ADR 0006
  Phase 6 gate). Workers run in this env, so no second manifest.
- `apps/api/app/config.py`: add an `--- Phase 6: Observability ---` block —
  `otel_exporter_otlp_endpoint: str | None = None`,
  `otel_service_name` default `workpulse`, per-worker `*_metrics_port` knobs
  (`relay_metrics_port: int = 9101` … `reports_metrics_port: int = 9105`;
  `0` = disabled).
- `.env.example`: document the new keys in a matching section.
- Verify: `cd apps/api && uv sync` resolves; `make check-api` green (nothing
  imports the new deps yet).

**T2 — Shared telemetry bootstrap + structlog correlation.**
- New `apps/api/app/shared/telemetry.py` (only place besides
  `app/shared/logging.py` allowed to import `opentelemetry`/`prometheus_client`
  per rule 4):
  - `configure_telemetry(settings)` → TracerProvider + MeterProvider;
    Prometheus exporter bridging OTel metrics into a `prometheus_client`
    registry served as text exposition; OTLP HTTP span exporter **only** when
    `OTEL_EXPORTER_OTLP_ENDPOINT` is set (BatchSpanProcessor, export failures
    dropped/logged — rule 6).
  - `trace_ids()` helper reading the active span's W3C `trace_id`/`span_id`
    (32-hex / 16-hex) for structlog binding.
  - Route-template resolver: matched FastAPI `route.path` template
    (e.g. `/api/v1/employees/{employee_id}`) or `UNMATCHED` — never raw
    paths/query strings (rule 5).
  - Bounded instruments matching the `## API contract` metric table:
    `workpulse_http_requests_total`, `workpulse_http_request_duration_seconds`,
    `workpulse_http_requests_in_flight`, `workpulse_worker_events_total`,
    `workpulse_worker_unit_duration_seconds`, plus a `generate_latest()`
    accessor for the `/metrics` response.
- `apps/api/app/shared/logging.py`: add a structlog processor binding
  `trace_id`/`span_id` from the active span next to `request_id` (absent →
  omit fields, never crash).
- Tests: `apps/api/tests/unit/test_telemetry.py` — span id formats, route
  template normalization (`UNMATCHED`, parameterized routes), metric label
  policy (no `tenant_id`/`employee_id`/`user_id`/`request_id` labels),
  processor no-op outside a span.
- Verify: `make check-api` green; domains untouched (no `opentelemetry`
  imports outside the two shared modules).

**T3 — API wiring: middleware, `X-Trace-Id`, `GET /metrics`.**
- `apps/api/app/main.py`:
  - Lifespan: `configure_telemetry` on startup, provider shutdown in
    `finally`.
  - Extend `RequestContextMiddleware` (or add one telemetry middleware beside
    it, keeping current order before CORS): extract W3C `traceparent`/
    `tracestate` best-effort (malformed → fresh root span, request never
    fails), start the HTTP server span, bind `trace_id`/`span_id` into
    structlog contextvars alongside `request_id`, set
    `response.headers["X-Trace-Id"]`, record the three HTTP instruments with
    the route template, set span status for 5xx without PII, and decrement
    `in_flight` in `finally` (edge cases above).
  - `GET /metrics` next to `/healthz` with `include_in_schema=False`,
    `text/plain; version=0.0.4; charset=utf-8`; exclude `/metrics` from
    self-instrumentation so scrape traffic cannot distort the error-rate
    panels.
- Tests: `apps/api/tests/integration/test_observability.py` — happy path
  (span recorded, 32-hex `X-Trace-Id`, structlog fields), valid `traceparent`
  adoption + echo, malformed `traceparent` → 200 with fresh id, route-template
  vs `UNMATCHED` labels on `/metrics`, PII/tenant-label isolation and
  cross-tenant 404 without tenant leakage on `/metrics`, error path (5xx span
  status + `in_flight` returns to 0).
- Verify: `make check-api` green including existing
  `tests/contract/test_openapi_drift.py` (zero drift — rule 7), then
  `make gen-api` produces no diff in `contracts/openapi/openapi.json` /
  `packages/api-client`; `make check-e2e` regression stays green.

**T4 — Worker instrumentation (relay → realtime → notifications → payroll →
reports).**
- Small per-unit helper in `app/shared/telemetry.py` (e.g.
  `worker_unit(worker, event_type)`) wrapping: root span per batch/message/job,
  `trace_id`/`span_id` structlog binding for the unit's duration, timing +
  `workpulse_worker_events_total{status="succeeded"|"failed"|"skipped"}` and
  `workpulse_worker_unit_duration_seconds` increments; failures recorded then
  re-raised to the existing loop/backoff handling — the loop must never die.
- Per-worker seam (one concern per change inside the task):
  - `workers/relay/relay.py` — per-row span in `_publish_batch` (row →
    `event_type` from payload type).
  - `workers/realtime/consumer.py` — per-message span in `_handle`.
  - `workers/notifications/consumer.py` — per-message span around
    `NotificationUnit` handling.
  - `workers/payroll/runner.py` — per-run span in `_drain` around
    `unit.calculate`.
  - `workers/reports/consumer.py` — per-message span in `_handle`.
  - Each `workers/*/main.py` `_run()` optionally starts a loopback-only
    `prometheus_client` HTTP listener on its configured `*_metrics_port`
    (skip when `0`/unset — tests and parallel runs never bind).
- Preserve the pinned `*_started`/`*_stopped` log lines exactly
  (`scripts/dev.sh` readiness grep depends on them).
- Tests in `apps/api/tests/unit/workers/` (and `tests/unit/outbox/` for relay):
  success → `status="succeeded"`, injected failure → `status="failed"` + loop
  continues, span/log correlation fields present, metrics port `0` binds
  nothing.
- Verify: `make check-api` green; `make dev` still boots all workers with the
  same readiness lines.

**T5 — Observability stack as code.**
- `ops/observability/prometheus/prometheus.yml`: scrape jobs for the API
  (`:8000/metrics`) and workers (`:9101`–`:9105`), matching the ports pinned
  in T1/T4.
- `ops/observability/grafana/provisioning/` (datasource → Prometheus
  `http://prometheus:9090`, anonymous Viewer) +
  `ops/observability/grafana/dashboards/workpulse-overview.json` with valid
  panels: HTTP p95 latency (`workpulse_http_request_duration_seconds`
  histogram_quantile), HTTP error rate, request throughput, in-flight
  requests, worker throughput/failures.
- `docker-compose.yml`: `prometheus` (`127.0.0.1:9090`) and `grafana`
  (`127.0.0.1:3001`) services mounting those configs, loopback-bound like the
  other infra.
- `Makefile`: `observability-up` / `observability-down` (dedicated so
  `make dev` / `make check` stay fast).
- Tests: `apps/api/tests/unit/test_observability_config.py` — parse
  `prometheus.yml` targets against the configured ports; validate the
  dashboard JSON defines the four required panel families and queries only
  bounded-label metrics (jsonschema already a dev dep).
- Verify: `make observability-up`, then `127.0.0.1:9090/-/ready` responds and
  Grafana boots with the dashboard pre-provisioned; `make check-api` green.

**T6 — Record and close.**
- `.ai/decisions/0023-observability-telemetry-and-metric-label-policy.md`:
  telemetry architecture (in-process OTel, Prometheus scrape of API + workers,
  OTLP opt-in), the metric label policy (route templates, forbidden
  high-cardinality labels, PII rule), and the exposition model (OTel
  Prometheus exporter bridged through `prometheus_client`, single
  `apps/api/pyproject.toml` dependency surface).
- Tick the acceptance criteria above, update `tasks/TODO.md` (Phase 6 row),
  and note Phase 6 status in `.ai/architecture.md` if its roadmap row needs it.
- Verify: `make check` full gate green.

### Risks & unknowns

- **`*_started` readiness lines are load-bearing** for `scripts/dev.sh` and
  e2e boots — instrumentation must not change worker startup logs (called out
  in T4).
- **`opentelemetry-exporter-prometheus` ↔ `prometheus_client` version
  coupling**: the exporter bridges OTel instruments into a `prometheus_client`
  registry; pin compatible versions in T1 and prove the exposition format in
  the T2/T3 tests. Fallback: record instruments directly with
  `prometheus_client` behind the same `app/shared/telemetry.py` API (record
  the final choice in ADR 0023's exposition model).
- **`BaseHTTPMiddleware` limitations** (streaming, exception propagation):
  the existing `RequestContextMiddleware` already sits there; if span/metric
  wrapping misbehaves under it, the fallback is a pure ASGI middleware — keep
  the seam narrow so the swap is local.
- **Test port collisions**: worker metric listeners default to `:9101`–`:9105`
  in dev but must be `0`/disabled under pytest (parallel runs), per the spec.
- **Pre-existing port drift** (compose dev Postgres on `:5434` vs documented
  `:5432`) is unrelated; do not fix it in this feature — flag it in the ADR or
  a TODO line only.
- **No trace backend in Compose**: traces are only correlatable via
  `X-Trace-Id`/logs unless `OTEL_EXPORTER_OTLP_ENDPOINT` points at an external
  collector — acceptable per PO decision 2, so the Grafana dashboards carry
  metrics only (no trace-exemplar panels in scope).
- **Host-gateway scrape reachability**: Prometheus runs in the compose bridge
  network and targets `host.docker.internal`. Docker Desktop forwards that to
  host loopback, while a plain Linux Docker engine maps it to the bridge
  gateway, where listeners bound strictly to `127.0.0.1` (the dev API and worker
  metrics servers) are not reachable. `make observability-up` readiness,
  dashboard provisioning and the config test remain the gate; live target data
  on Linux needs a reachable bind (recorded in ADR 0023, Consequences).

### Test strategy

| Gate | Tests | Failure cases covered |
| --- | --- | --- |
| `make check-api` (unit) | `tests/unit/test_telemetry.py`, `tests/unit/test_observability_config.py`, worker unit tests in `tests/unit/workers/` + `tests/unit/outbox/` | span fields omitted outside a span; label cardinality (UUID paths collapse to template/`UNMATCHED`); worker failure → `status="failed"`, loop survives; metrics port `0` binds nothing; dashboard/config target mismatches |
| `make check-api` (integration) | `tests/integration/test_observability.py` | malformed `traceparent` → 200 + fresh `X-Trace-Id`; 5xx → span error status, `in_flight` gauge returns to 0; cross-tenant 404 leaks nothing on `/metrics`; OTLP unset → no export attempted |
| `make check-api` (contract) | existing `tests/contract/test_openapi_drift.py` | `GET /metrics` must not appear in `contracts/openapi/openapi.json`; `make gen-api` zero diff |
| `make check-e2e` | existing `tests/e2e/full-hop.sh` regression only | worker readiness lines unchanged; app boots with telemetry enabled |
| `make check-web` | none added (web untouched) | — |
