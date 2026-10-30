# Feature: Reliability (load testing, rate limiting, circuit breakers)

- **Status:** approved
- **Phase:** 7
- **Size:** M
- **Surfaces:** api+web+events+workers (event contracts unchanged — the
  template ladder's only value covering `api` + `web` + `workers`; real work is
  the shared rate-limit middleware and circuit breakers in `apps/api`, breakers
  on outbound worker clients, web 429/503 copy, and the k6 load gate in
  `tests/performance/`)
- **Links:** `.ai/product.md` (Success metrics row 7: "Load test sustains
  target check-in throughput with p95 < 300ms API latency") ·
  `.ai/architecture.md` (Phase roadmap row 7: `load testing, rate limiting,
  circuit breakers` — "reliability becomes a requirement, not a hope") ·
  `tasks/doc.md` §3 (Redis responsibilities: "API rate limiting") + §11 V7 ·
  `.ai/testing.md` (gates table: `Load | Phase 7` — a gate that is not wired
  into `make check` does not protect anything) · ADR 0012 (outbox relay,
  ordering, duplicate window) · ADR 0015 (notification delivery + bounded
  retries) · ADR 0022 (MinIO document storage) · ADR 0023 (telemetry + metric
  label policy) · `.ai/features/observability.md` ·
  `.ai/features/employee-documents.md` · `.ai/features/outbox-relay.md` ·
  `.ai/features/notifications.md`

## Problem

WorkPulse now runs a FastAPI monolith plus five workers over PostgreSQL, Redis,
Kafka, ClickHouse and MinIO, with real outbound calls to Resend and Slack — yet
it has no defense against overload or dead dependencies. The login endpoint is
brute-forceable, any client can hammer mutations unbounded, a hung MinIO or
Kafka call can stall a request or a worker loop, and no gate proves the
check-in path sustains target throughput. Phase 7 (brief V7) exists to make
reliability a measured requirement: distributed load testing, rate limiting and
circuit breakers.

## Business rules

1. **Rate limiting — two tiers, Redis-backed sliding window** (the brief gives
   Redis the rate-limiting job):
   - **Auth tier:** `POST /auth/login` and `PUT /auth/password`, keyed **per
     client IP** (brute-force protection). Default budget: 30 requests / 60s.
   - **Mutation tier:** every authenticated mutating route (`POST`/`PUT`/
     `PATCH`/`DELETE` under `/api/v1`), keyed **per session user id**. Default
     budget: 120 requests / 60s.
   - Reads (`GET`) are unthrottled; `GET /healthz` and `GET /metrics` are
     exempt entirely. Budget numbers are Settings-tunable defaults
     (`RATE_LIMIT_*`), sized so the check-load profile and the e2e suite pass
     with ≥ 2× headroom (see rule 5).
2. **Enforcement is one shared middleware** (`app/shared/rate_limit.py`,
   wired in `app/main.py` alongside the telemetry middleware): it applies the
   tier by route before any router/service work. No per-router opt-in
   dependencies — a new route is covered by default.
3. **Throttle response:** `429` in the house error shape
   `{"error": {"code": "rate_limited", "message": "…"}}` plus a `Retry-After`
   header (integer seconds until the window resets). A throttled request
   mutates nothing: no audit entry, no event.
4. **Limiter fail-open.** If Redis is unreachable or slow (bounded short
   timeout), the request proceeds and the condition is logged (no PII) and
   counted in metrics — reliability machinery never breaks user traffic, the
   same rule observability adopted for exporters.
5. **Headroom rule.** Default budgets must let the k6 load profile and the
   Playwright suite pass unthrottled; if a gate trips the limiter, the fix is
   budget/fixture design (more synthetic users, wider window), never disabling
   the gate.
6. **Circuit breakers wrap only non-critical-path outbound calls:**
   - relay → Kafka publish (`workers/relay/publisher.py`),
   - MinIO document storage (`apps/api/app/infrastructure/storage/minio.py`),
   - notification providers Resend + Slack
     (`apps/api/app/infrastructure/notifications/`).
   PostgreSQL, Redis and ClickHouse stay unwrapped — in this monolith their
   failure is total, so a breaker would only add latency to a dead path.
7. **Breaker behavior:** closed → open after 5 consecutive failures; open →
   fail fast with no outbound I/O; after a 30s cooldown → half-open admitting
   **exactly one** trial call (no stampede); success closes, failure re-opens.
   Threshold/cooldown are Settings-tunable (`CIRCUIT_BREAKER_*`). The breaker
   is shared plumbing (`app/shared/circuit_breaker.py`) applied at
   infrastructure adapters and worker call sites — domain packages never
   import it (architecture rule 3).
8. **Fail-fast mapping.** HTTP surfaces answer an open breaker with `503` and
   the house error code `dependency_unavailable` — never a hang, never a bare
   500. Worker units fail fast and fall into their existing policies (ADR 0015
   bounded retries for deliveries; the relay keeps rows unpublished with its
   backoff) — the worker loop never dies and never blocks on an open breaker.
9. **Observable under ADR 0023 conventions.** Breaker state and throttle
   rejections are metrics with bounded labels only (`dependency` ∈ fixed set,
   `tier` ∈ {`auth`,`mutation`}); no `tenant_id`/`user_id`/`employee_id`
   labels, no PII anywhere.
10. **Load gate is a command.** `make check-load` runs k6 scenario code in
    `tests/performance/` with thresholds as code and joins `make check` (like
    `check-e2e`). The smoke profile: 60s sustained **25 check-ins/s** plus
    manager board reads against the local stack, **p95 < 300ms**, **0 errors**
    (no 5xx, no unexpected 4xx) — this pins the product metric's "target
    check-in throughput" at 25/s.
11. **Deterministic load fixtures.** The k6 scenarios authenticate as seeded
    synthetic employees (`.ai/testing.md` rule 6: synthetic people only),
    provisioned idempotently by the check-load path and spread across enough
    distinct users that rule 1's mutation budget has ≥ 2× headroom at 25
    check-ins/s.
12. **No OpenAPI or `@workpulse/api-client` drift.** 429 and 503 reuse the
    existing error shape; middleware and breakers add no routes.
    `make gen-api` produces no diff.

## Scope

- `apps/api/app/shared/rate_limit.py` — Redis sliding-window limiter + shared
  FastAPI/ASGI middleware, with a `RATE_LIMIT_*` Settings block (enabled flag,
  per-tier budgets/windows; documented in `.env.example`).
- `apps/api/app/shared/circuit_breaker.py` — breaker state machine (closed /
  open / half-open) with a `CIRCUIT_BREAKER_*` Settings block, applied in the
  MinIO storage adapter, `workers/relay/publisher.py`, and the Resend/Slack
  adapters.
- Stable error codes in `apps/api/app/shared/errors.py`: `rate_limited` (429 +
  `Retry-After`) and `dependency_unavailable` (503).
- Limiter + breaker metrics on the ADR 0023 conventions (bounded labels).
- k6 scripts + fixture provisioning in `tests/performance/`, and a
  `make check-load` target wired into `make check`.
- Web: map `rate_limited` and `dependency_unavailable` to pinned user copy in
  the shared error handling + `apps/web/messages/{en,vi}/` catalogs (en + vi
  parity per `.ai/conventions.md` i18n rules).
- Tests: unit (breaker state machine, window math — no I/O) + integration
  (429 behavior against real Redis with an injected clock; breaker open →
  half-open → closed against stubbed clients) + e2e regression stays green.
- ADR 0025 recording the reliability architecture (limiter model, breaker
  placement/semantics, load gate) — expensive to reverse.

## Out of scope

- Per-tenant quotas, usage caps, API keys, plan tiers or billing-style limits.
- Rate limiting on reads, WebSocket connections, or the realtime surface.
- Circuit breakers on PostgreSQL, Redis or ClickHouse clients (their failure is
  total in the monolith).
- DDoS/WAF/edge rate limiting, load shedding queues, bulkheads, autoscaling —
  deployment-edge concerns (Phase 8) or future needs.
- A retry framework beyond what workers already do (ADR 0015 bounded retries,
  relay backoff).
- Browser RUM or load-testing the Next.js app: the gate loads the API only.
- Alertmanager/SLO burn-rate paging (already out of scope in the observability
  spec).

## Surfaces & files

| Surface | Location |
| --- | --- |
| Shared plumbing | `apps/api/app/shared/rate_limit.py` · `apps/api/app/shared/circuit_breaker.py` · `apps/api/app/shared/errors.py` · `apps/api/app/config.py` · `apps/api/app/main.py` |
| API domain | — (domains stay untouched; breakers live in infrastructure adapters) |
| API router | — (no new routes; the middleware covers `apps/api/app/api/v1/*`) |
| Infrastructure clients | `apps/api/app/infrastructure/storage/minio.py` · `apps/api/app/infrastructure/notifications/resend_email.py` · `apps/api/app/infrastructure/notifications/slack_web.py` |
| Events | — (no event schema changes) |
| Workers | `workers/relay/publisher.py` (Kafka publish breaker) · `workers/notifications/` (provider call sites) |
| Web feature | shared `ApiError.code` → copy mapping (error helpers) · `apps/web/messages/en/*.json` + `apps/web/messages/vi/*.json` |
| Load tests | `tests/performance/` (k6 scenarios + thresholds as code + fixture provisioning) |
| API tests | `apps/api/tests/unit/` (breaker/limiter pure logic) · `apps/api/tests/integration/` (Redis limiter, breaker lifecycle) |
| E2E | `tests/e2e/` — regression stays green (logins must not trip the auth tier) |
| Infra | `Makefile` (`check-load`, wired into `check`) · `.env.example` (`RATE_LIMIT_*`, `CIRCUIT_BREAKER_*`) |

## Roles & access

- No RBAC matrix rows in `.ai/security.md` change. Limits apply uniformly to
  every role — an Admin hammering mutations is throttled like anyone else.
- The mutation tier is keyed per session user, so budgets are inherently
  tenant-scoped; the auth tier's only shared budget is the client IP.
- `GET /healthz` and `GET /metrics` stay exempt and reachable while throttling
  is active (operators must see the system under pressure).
- Breakers are not an access concern: `503 dependency_unavailable` reveals no
  role, tenant or resource state.

## API contract

No new routes. Two error responses are pinned on the existing surface:

| Response | Status | Body (house shape) | Headers | When |
| --- | --- | --- | --- | --- |
| Throttled | `429` | `{"error": {"code": "rate_limited", "message": "…"}}` | `Retry-After: <int seconds>` | tier budget exhausted within the window |
| Dependency open | `503` | `{"error": {"code": "dependency_unavailable", "message": "…"}}` | — | a wrapped dependency's breaker is open (document storage, or API-side provider use) |

| Tier | Routes | Key | Default budget |
| --- | --- | --- | --- |
| auth | `POST /auth/login` · `PUT /auth/password` | client IP | 30 req / 60s |
| mutation | every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` (incl. `POST /auth/logout`) | session user id | 120 req / 60s |
| exempt | all `GET` · `GET /healthz` · `GET /metrics` | — | — |

## Events

None added or changed.

## Edge cases

- **The gate must not throttle itself:** the k6 profile spreads check-ins over
  ≥ the synthetic user pool (rule 11) and the Playwright logins fit the auth
  tier with headroom; if either trips 429, that is a spec-level failure of the
  headroom rule, not a flake to ignore.
- **Redis down or slow mid-traffic:** fail-open (rule 4) — requests proceed,
  a PII-free warning is logged and counted; nothing about the limiter may add
  tail latency to a healthy path.
- **Shared IP, different users:** two users (even across tenants) behind one
  IP share only the auth tier; their mutation budgets are independent —
  proven by a test.
- **`Retry-After` ignored by the client:** further attempts inside the same
  window keep returning 429; nothing crashes, no audit noise.
- **Document upload with the MinIO breaker open:** the upload-intent call
  fails `503 dependency_unavailable` before any row/audit/outbox write (no
  orphan state); the confirm and download paths fail the same fast way.
- **Relay with the Kafka breaker open:** outbox rows stay unpublished and
  `last_error` bookkeeping continues; API writes are unaffected (already
  proven in outbox-relay); on recovery the backlog publishes with per-employee
  ordering intact (ADR 0012) and the duplicate `event_id` window unchanged.
- **Notification provider breaker open:** each delivery unit fails fast into
  ADR 0015's bounded retries and a terminal `failed`/`skipped` state; the
  consumer keeps processing other messages; error text stays payload-free.
- **Half-open stampede:** exactly one trial call is admitted while half-open;
  concurrent callers fail fast until the trial resolves.
- **Load-run anomalies:** any 429 or 503 during the k6 smoke fails the
  error-rate threshold — the profile must never trigger the limiter or an open
  breaker.

## Acceptance criteria

- [ ] API (auth tier throttle): given 31 `POST /auth/login` attempts from one
      IP within 60s at default settings, when the 31st arrives, then it returns
      `429 {"error":{"code":"rate_limited"}}` with `Retry-After`, and the
      request never reaches the auth service.
- [ ] API (mutation tier + budget independence): given one user exceeds 120
      mutations/60s while a second user stays under, when the first user
      mutates again, then `429 rate_limited`; the first user's `GET` reads
      still return 200 and the second user's mutations still return 2xx
      (per-user budgets, not shared).
- [ ] API (failure — Redis unreachable): given Redis is down, when a request
      passes the middleware, then it proceeds (fail-open, 2xx) and the warning
      log contains no PII.
- [ ] API (breaker — fail fast): given the MinIO dependency has failed past the
      threshold and the breaker is open, when a document route calls storage,
      then it returns `503 {"error":{"code":"dependency_unavailable"}}` without
      waiting on MinIO (bounded fast failure, no orphan rows).
- [ ] API (breaker — recovery): given the breaker is open, when the dependency
      recovers and the cooldown elapses, then the single half-open trial
      succeeds, the breaker closes, and subsequent requests return 2xx.
- [ ] API (access semantics unchanged): given throttled and unthrottled
      requests, when a cross-tenant read and a denied-role mutation are sent
      under budget, then they still return `404` and `403` respectively —
      the middleware changes no authorization behavior.
- [ ] Workers (relay breaker): given Kafka is down, when the relay attempts to
      publish, then the breaker opens after the threshold and further attempts
      fail fast without blocking; outbox rows remain unpublished while API
      writes keep succeeding; after Kafka recovers and the cooldown elapses,
      the backlog publishes in per-employee order.
- [ ] Workers (notifications breaker — failure): given the email provider is
      down, when the notifications consumer processes a delivery, then the
      unit fails fast (no hang), bounded retries end in a terminal
      `failed`/`skipped` state, and the consumer loop keeps processing
      subsequent messages.
- [ ] Web (429 copy): given a mutation returns `429 rate_limited`, when the UI
      handles the error, then the pinned rate-limited copy renders (toast/error
      state) in the active locale with en + vi catalog parity — never raw
      server text.
- [ ] Web (503 copy): given a document request returns
      `503 dependency_unavailable`, then the pinned unavailable copy renders in
      the surface's error state.
- [ ] Contract (no drift): given the middleware and error codes, when
      `make gen-api` runs, then `contracts/openapi/openapi.json` and
      `packages/api-client` have zero diff.
- [ ] Load (`make check-load`, wired into `make check`): given the local stack
      with load fixtures, when the k6 smoke runs 60s at a sustained 25
      check-ins/s plus board reads, then p95 API latency < 300ms with 0 errors
      (no 5xx, no 429/503), and breaching the thresholds fails the command.
- [ ] Integration (limiter determinism): given an injected clock and an
      explicit small budget (e.g. 3 req / 60s), when 4 requests arrive in one
      window, then exactly one returns `429` with a correct `Retry-After`, and
      the window resets on the clock — no wall-clock sleeps in tests.

## Decisions (PO, 2026-10-30)

1. **Rate-limit scope — auth + mutations.** Login/password endpoints are
   keyed per client IP (brute-force); all authenticated mutations are keyed
   per session user; reads unthrottled. Redis sliding-window counters
   (brief §3 assigns rate limiting to Redis).
2. **Enforcement point — shared middleware.** One limiter middleware in
   `app/shared/` applies tiers by route before router work, with uniform `429`
   + `Retry-After`; no per-router dependency that a new route could forget.
3. **Circuit breakers — Kafka + MinIO + providers, fail-fast.** Breakers wrap
   the relay's Kafka publish, the MinIO storage client and the Resend/Slack
   adapters; an open breaker fails fast (`503 dependency_unavailable` on HTTP
   surfaces; worker units fail fast into existing retry policies). PostgreSQL,
   Redis and ClickHouse stay unwrapped — in the monolith their failure is
   total.
4. **Load tooling — k6.** Scenarios and SLO thresholds as code in
   `tests/performance/`; deterministic, CI-friendly, one static binary —
   "gates are commands, not opinions".
5. **Load gate + SLO — `make check-load` smoke in `make check`.** ~60s k6
   smoke at a sustained 25 check-ins/s plus reads with p95 < 300ms and 0
   errors, wired into `make check` like `check-e2e`. This pins the product
   metric's "target check-in throughput" at 25/s.

Implementation defaults not covered above (tier budgets/windows, breaker
failure threshold and cooldown, Settings names) are proposed by this spec in
the Business rules as tunable values, with the headroom rule as their
constraint.

## Plan

Repo research (read-only) confirmed the spec's assumptions: house error shape
and handlers live in `app/shared/errors.py` (`WorkPulseError` +
`register_error_handlers`), middleware is `BaseHTTPMiddleware` registered in
`app/main.py` (CORS → Telemetry → RequestContext → routes), Settings are flat
pydantic-settings fields (`RATE_LIMIT_*`/`CIRCUIT_BREAKER_*` blocks fit), Redis
is the lazy `get_redis()` singleton in `app/infrastructure/redis/client.py`
(already fail-fast tuned: connect 0.5s / socket 1s), ADR 0023 instruments are
created in `app/shared/telemetry.py` `create_telemetry()` with a forbidden-label
set and label-policy tests, the MinIO adapter is `MinioDocumentStorage` with an
injectable `S3Client` protocol seam, the relay publisher has no internal retry
(failures already bookkeep per row and the loop survives), the notifications
`_deliver` loop already turns any exception into bounded retries + terminal
`failed`, e2e does ~22 real logins per run from one IP, and `tests/performance/`
does not exist yet. Event contracts are untouched, so the "pin the contract
first" rule is already satisfied by the `## API contract` section above;
`tests/contract/test_openapi_drift.py` + `make gen-api` keep the surfaces from
drifting (rule 12).

Research gaps this plan resolves explicitly (implementation defaults, per
`## Decisions` closing note):

- **Client IP extraction is net-new.** The middleware reads
  `X-Forwarded-For` (first hop) with `request.client.host` fallback; used for
  the auth-tier key and the mutation-tier fallback key only.
- **Middleware cannot `Depends`-inject identity.** The mutation-tier key
  resolves the session cookie through the same auth service seam
  (`dependencies.get_current_user` / `AuthService.me`) for the *user id only*;
  router dependency resolution is untouched. One extra session lookup per
  mutation is accepted (mutations are budgeted to 120/60s anyway).
- **`POST /auth/logout` without a session** has no user id (idempotent 204
  path) — the mutation tier keys it by client IP fallback so it is still
  bounded.
- **`Retry-After` needs headers on errors.** `WorkPulseError` gains an optional
  `headers` mapping merged into the handler's `JSONResponse`; `RateLimitedError`
  (429) supplies `Retry-After`. The limiter middleware raises it before any
  router/service work, so a throttled request mutates nothing. The breaker's
  `CircuitOpenError` (from `app/shared/circuit_breaker.py`) maps to 503
  `dependency_unavailable` via its own handler — adapters raise it, domains
  never import it (architecture rule 3).
- **MinIO has no explicit timeouts today** (botocore defaults ~60s). The
  storage adapter gains bounded connect/read timeouts alongside the breaker —
  the breaker alone cannot make a *single* hung call fail fast.
- **E2E auth-tier headroom is thin in the worst case** (~22 logins/run vs the
  30/60s default). The sliding window only counts logins inside any 60s span;
  the gate task measures per-window occupancy and, only if a window would
  exceed the budget, applies fixture design (login reuse/spacing in
  `tests/e2e/` helpers) — never disabling the gate, per the headroom rule.

### Acceptance criteria → change map

| AC | Satisfied by |
| --- | --- |
| Auth tier throttle (31st login → 429 + `Retry-After`, never reaches auth service) | T1 (`RateLimitedError` + headers) · T6 (window math) · T7 (middleware short-circuit) + integration test in T7 |
| Mutation tier + per-user budget independence | T6/T7 (key = session user id) + integration test in T7 |
| Redis unreachable → fail-open, PII-free warning | T6 (bounded-timeout fail-open + log/metric) + integration test in T7 |
| Breaker fail fast (MinIO open → 503, no hang, no orphan rows) | T2 (state machine) · T3 (storage adapter + 503 mapping + timeouts) + integration test in T3 |
| Breaker recovery (cooldown → single half-open trial → closed) | T2 · T3 integration test |
| Access semantics unchanged (404 cross-tenant, 403 denied role under budget) | T7 integration test |
| Relay breaker (fail fast, rows unpublished, loop alive, ordered recovery) | T4 + worker test |
| Notifications breaker (fail fast into bounded retries → terminal `failed`, loop alive) | T5 + unit test |
| Web 429 copy (en + vi parity, never raw server text) | T8 (shared error→copy mapping + catalogs) + web test |
| Web 503 copy | T8 |
| Contract no drift (`make gen-api` zero diff) | T1 (codes only, no routes) + existing `test_openapi_drift.py` |
| `make check-load` in `make check` (25 check-ins/s, p95 < 300ms, 0 errors) | T9 (k6 scenarios, fixtures, Makefile) |
| Limiter determinism (injected clock, no sleeps) | T6 unit tests (window math) + T7 integration test (clock-driven app) |

### Ordered tasks

Each task keeps `make check` green on its own; tests ship with the code they
cover. Backend lands before frontend; no event contracts are written (none are
needed).

**T1 — Pin the error contract (shared errors, headers, codes).**
- `apps/api/app/shared/errors.py`: add `RateLimitedError(WorkPulseError)`
  (`status_code=429`, `code="rate_limited"`, carries `retry_after: int`,
  exposes `headers`) and `DependencyUnavailableError(WorkPulseError)` /
  `CircuitOpenError` mapping target (`status_code=503`,
  `code="dependency_unavailable"`); give `WorkPulseError` an optional `headers`
  mapping that `register_error_handlers` merges into its `JSONResponse`.
- Tests: `apps/api/tests/unit/test_errors.py` — shape
  `{"error":{"code":...,"message":...}}`, `Retry-After` header emission, 503
  mapping.
- Verify: `make check-api`; `make gen-api` produces no diff (no routes added).

**T2 — Circuit breaker core + metrics.**
- `apps/api/app/shared/circuit_breaker.py`: closed/open/half-open state machine
  — open after `CIRCUIT_BREAKER_FAILURE_THRESHOLD` (default 5) consecutive
  failures, fail fast with no outbound I/O while open, `CIRCUIT_BREAKER_COOLDOWN_SECONDS`
  (default 30) then half-open admitting **exactly one** trial call (concurrent
  callers fail fast until the trial resolves), success closes, failure
  re-opens. Instance-per-dependency with a fixed `dependency` label from a
  closed vocabulary (`minio`, `kafka`, `resend`, `slack`). Injectable clock
  (`Callable[[], float]` monotonic, house clock-injection pattern).
- `apps/api/app/config.py` + `.env.example`: `CIRCUIT_BREAKER_ENABLED`,
  `CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `CIRCUIT_BREAKER_COOLDOWN_SECONDS`.
- `apps/api/app/shared/telemetry.py`: `workpulse_circuit_breaker_state`
  (gauge-style one-hot per `dependency`/`state`, precedent
  `cdc_connector_state`) + `workpulse_circuit_breaker_rejected` counter
  (`dependency` only) + transition counter; label-policy tests updated
  (bounded labels, forbidden set untouched).
- Tests: `apps/api/tests/unit/test_circuit_breaker.py` — threshold trip, no I/O
  while open, cooldown → single half-open trial (concurrent rejection), success
  closes / failure re-opens, disabled setting passes through. Pure logic, fake
  clock, no I/O.

**T3 — MinIO adapter breaker + timeouts + 503 mapping.**
- `apps/api/app/infrastructure/storage/minio.py`: wrap `self._client` calls
  (`create_upload`, `stat`, `copy`, `delete`, `presign_download`) with the
  `minio` breaker; add bounded connect/read timeouts to the boto3 `Config`;
  `CircuitOpenError` propagates out of the adapter.
- `apps/api/app/main.py` (or `register_error_handlers`): map `CircuitOpenError`
  → `503 {"error":{"code":"dependency_unavailable"}}`.
- Tests: integration (`apps/api/tests/integration/`) — stub `S3Client` failing
  past the threshold → document route returns 503 `dependency_unavailable`
  fast (no MinIO wait), no orphan rows/audit/outbox (upload-intent fails before
  any write); cooldown elapses (injected clock) → single trial succeeds →
  breaker closes → 2xx. Unit test for timeout config presence.

**T4 — Relay publisher (Kafka) breaker.**
- `workers/relay/publisher.py`: wrap `producer.send_and_wait` with the `kafka`
  breaker; open → `CircuitOpenError` raised before any broker I/O.
- Behavior preserved: `OutboxRelay._publish_batch` already bookkeeps
  `attempts`/`last_error` per row, stops the batch (strict per-employee order,
  ADR 0012), backs off, and never dies — the breaker only makes the failure
  bounded. Nothing in `relay.py` changes except what tests prove necessary.
- Tests: `apps/api/tests/unit/workers/` (or integration) — stub publisher/
  broker failing past threshold → further publishes fail fast, loop alive,
  rows stay unpublished, API writes unaffected; after recovery + cooldown the
  backlog publishes in per-employee order with the duplicate `event_id` window
  unchanged.

**T5 — Notification provider breakers (Resend + Slack).**
- `apps/api/app/infrastructure/notifications/resend_email.py` + `slack_web.py`:
  wrap the outbound `send` calls with `resend`/`slack` breakers (constructed in
  `workers/notifications/main.py` wiring or lazily in the adapters).
- Behavior preserved: an open breaker raises `CircuitOpenError`, which lands in
  `NotificationService._deliver`'s generic handler → fast bounded retries
  (3 attempts, injected sleeper) → terminal `failed`, payload-free error text
  (ADR 0015). `NotConfiguredError` → `skipped` path untouched.
- Tests: unit — provider down past threshold → delivery unit fails fast (no
  hang), bounded retries end terminal `failed`/`skipped`, consumer loop keeps
  processing subsequent messages; error text payload-free.

**T6 — Rate limiter core (Redis sliding window, fail-open).**
- `apps/api/app/shared/rate_limit.py`: sliding-window counter over `get_redis()`
  (sorted-set or two-window approximation — document the choice in ADR 0025),
  `clock`/`monotonic` injectable per the house pattern; two tiers with
  Settings-tunable budgets/windows; fail-open on Redis errors/timeouts (the
  existing 0.5s/1s socket timeouts bound the wait) with a PII-free warning log
  + `workpulse_rate_limit_fail_open` counter.
- `apps/api/app/config.py` + `.env.example`: `RATE_LIMIT_ENABLED`,
  `RATE_LIMIT_AUTH_MAX_REQUESTS` (30) / `RATE_LIMIT_AUTH_WINDOW_SECONDS` (60),
  `RATE_LIMIT_MUTATION_MAX_REQUESTS` (120) /
  `RATE_LIMIT_MUTATION_WINDOW_SECONDS` (60).
- `apps/api/app/shared/telemetry.py`: `workpulse_rate_limit_throttled` counter
  (`tier` ∈ {`auth`,`mutation`}) — bounded labels only, ADR 0023 policy tests
  updated.
- Tests: `apps/api/tests/unit/test_rate_limit.py` — window math with fake clock
  (3/60s budget, 4th request rejected, `Retry-After` counts down, window resets
  on the clock — no wall-clock sleeps), key isolation, fail-open path.

**T7 — Rate-limit middleware wiring + integration tests.**
- `apps/api/app/main.py`: register `RateLimitMiddleware` alongside the telemetry
  middleware (its 429s must be visible in `workpulse_http_requests`; document
  final order). Tier by route: `POST /auth/login` + `PUT /auth/password` →
  auth tier keyed by client IP (XFF first hop, `request.client.host` fallback);
  every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` (incl.
  `POST /auth/logout`) → mutation tier keyed by session user id, falling back
  to client IP when no session resolves; all `GET`, `/healthz`, `/metrics`
  exempt. Short-circuit with `RateLimitedError` (429 + `Retry-After`) before
  router/service work — no audit, no event.
- App seam for tests: the limiter is constructed with the settings + clock and
  reachable from the app (e.g. `app.state`) so integration tests can build the
  app with an injected clock and a small explicit budget.
- Tests: `apps/api/tests/integration/` against real Redis (:6380) with injected
  clock — (a) 31st login from one IP → 429 `rate_limited` + `Retry-After`, auth
  service never invoked; (b) user A over budget → 429 while A's `GET`s stay 200
  and user B's mutations stay 2xx (independent budgets); (c) Redis down →
  request proceeds 2xx, warning log PII-free; (d) cross-tenant read → 404 and
  denied-role mutation → 403 under budget (middleware changes no authorization);
  (e) limiter determinism AC (explicit 3/60s budget, clock-driven reset).

**T8 — Web error copy (429 / 503, en + vi).**
- Shared helper `apps/web/lib/api-errors.ts` (or the existing error-helper
  seam): map `ApiError.code === "rate_limited"` (or status 429) and
  `"dependency_unavailable"` (or status 503) to pinned copy before any
  per-feature fallback; wire the per-feature `errorMessage` helpers
  (`features/leave`, `employees`, `attendance`, `users`, `auth`, `documents`,
  `payroll`, `reports`, `organizations`) to consult it first — one shared
  mapping, no raw server text.
- `apps/web/messages/en/common.json` + `apps/web/messages/vi/common.json`:
  `common.errors.rateLimited` ("You're doing that too often. Please wait a
  moment and try again." / "Bạn thao tác quá thường xuyên. Vui lòng đợi một
  lát rồi thử lại.") and `common.errors.dependencyUnavailable` ("That service
  is temporarily unavailable. Please try again shortly." / "Dịch vụ tạm thời
  không khả dụng. Vui lòng thử lại sau.") — en + vi parity (parity test
  enforced).
- Tests: `apps/web` unit tests for the mapping (codes + status fallbacks),
  catalog parity; `make check-web`.

**T9 — k6 load gate (`tests/performance/` + `make check-load`).**
- `tests/performance/`: k6 scenario code with thresholds as code — smoke:
  60s sustained 25 check-ins/s (authenticated synthetic employees) + manager
  board reads against the local stack; `thresholds`:
  `http_req_duration{...}: p(95)<300`, error rate 0 (no 5xx, no 429/503).
  Deterministic fixture provisioning in the check-load path (idempotent
  synthetic users per `.ai/testing.md` rule 6), spread across enough distinct
  users that the mutation tier has ≥ 2× headroom at 25 check-ins/s (rule 11).
- `Makefile`: `check-load` target (compose stack up + migrate + seed +
  fixtures + k6 run, k6 binary presence checked with a clear failure message),
  wired into `check` like `check-e2e` (`check: check-api check-web check-e2e
  check-load`).
- Headroom verification (rule 5): measure per-60s-window login occupancy in
  `make check-e2e`; if any window approaches the 30/60s auth budget, fix by
  fixture design (login reuse/spacing in `tests/e2e/` helpers) — never by
  disabling the gate. Any 429/503 during the k6 smoke fails the error-rate
  threshold by design.
- Tests/verify: `make check-load` fails when thresholds are breached (prove by
  temporarily lowering one threshold or raising rate in a scratch run);
  `make check` stays green with the new target.

**T10 — ADR + docs + spec close-out.**
- `.ai/decisions/0025-reliability-rate-limiting-circuit-breakers-and-load-gate.md`:
  limiter model (sliding window + key/tier table + fail-open), breaker
  placement/semantics (threshold/cooldown/single half-open trial; why
  PostgreSQL/Redis/ClickHouse stay unwrapped; MinIO timeout addition), load
  gate shape (k6 smoke, 25/s pin, thresholds as code), and the mutation-tier
  IP fallback for sessionless mutations.
- Update `.ai/testing.md` gates table (Load gate now wired),
  `tasks/TODO.md`, and this spec's acceptance checkboxes. `AGENTS.md` /
  `.ai/` only if conventions changed.

### Risks & unknowns

- **E2E auth headroom** (biggest gate risk): 22 logins/run from one IP vs the
  30/60s default. Mitigation is measurement + fixture design (T9); if Playwright
  trips 429 that is a spec-level failure of the headroom rule, not a flake.
- **Middleware identity resolution cost**: one extra session lookup per
  mutation; acceptable at 120/60s, but revisit if profiling disagrees. The
  lookup reuses the auth service seam; it must not change router semantics.
- **k6 availability**: `make check-load` needs the k6 binary on PATH; the
  target fails loudly if missing. If CI cannot install k6, the gate placement
  in `make check` needs a PO revisit (documented in ADR 0025).
- **k6 runtime cost** in `make check`: ~1–2 min added per gate run alongside
  check-e2e; accepted per "gates are commands".
- **MinIO timeout values**: bounded timeouts are a behavior change beyond the
  breaker wrap (previously unbounded botocore defaults); values sized for large
  uploads, recorded in ADR 0025.
- **Half-open trial across processes**: breaker state is per-process (in-memory)
  — with a single uvicorn + one worker per worker-process this matches the
  deployment; multi-process deployments would need shared state (out of scope,
  noted in ADR 0025).

### Test strategy

| Layer | Where | Covers (failure case) | Gate |
| --- | --- | --- | --- |
| Unit | `apps/api/tests/unit/test_rate_limit.py` | window math w/ fake clock — exactly one 429 at budget+1, correct `Retry-After`, reset on clock (determinism AC) | `make check-api` |
| Unit | `apps/api/tests/unit/test_circuit_breaker.py` | threshold trip, fail-fast no-I/O while open, single half-open trial (stampede rejection), recovery re-open | `make check-api` |
| Unit | `apps/api/tests/unit/test_errors.py` | house shape, `Retry-After` header, 503 code mapping | `make check-api` |
| Unit | `apps/api/tests/unit/test_telemetry.py` (extend) | new instruments respect the bounded-label policy | `make check-api` |
| Unit | `apps/api/tests/unit/workers/` | relay/notifications fail fast into existing policies, loop alive, payload-free errors | `make check-api` |
| Integration | `apps/api/tests/integration/` (real Redis :6380, injected clock, stubbed `S3Client`) | 31st-login 429; budget independence across users; Redis-down fail-open (PII-free); MinIO breaker 503 with no orphan rows + recovery; access semantics 404/403 unchanged | `make check-api` |
| Web unit | `apps/web` tests | 429/503 → pinned copy mapping, en/vi catalog parity | `make check-web` |
| E2E regression | `tests/e2e/` | stays green; logins fit the auth tier (headroom measurement) | `make check-e2e` |
| Load | `tests/performance/` (k6) | 60s @ 25 check-ins/s + board reads: p95 < 300ms, 0 errors (no 429/503); threshold breach fails the command | `make check-load` (in `make check`) |

### Contract pin (full-stack rule)

No new routes and no event contracts, so the API contract is already pinned by
the `## API contract` table (429 + `Retry-After`, 503 `dependency_unavailable`,
house shape). Backend tasks (T1–T7) land before the frontend task (T8), and
`tests/contract/test_openapi_drift.py` + `make gen-api` prove zero drift
(rule 12).
