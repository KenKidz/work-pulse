# 25. Reliability: Redis sliding-window limits, per-process circuit breakers and the k6 load gate

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

`.ai/features/reliability.md` (Phase 7, PO decisions 1–5) makes reliability a
measured requirement: the monolith plus five workers now calls out to Kafka,
MinIO and the Resend/Slack APIs, while the check-in path has a throughput and
latency target. The feature must satisfy:

- Brute-force protection on login/password and a mutation budget for every
  authenticated write, with Redis as the counter store (brief §3).
- One enforcement point that covers new routes by default, not per-router
  opt-in dependencies.
- A throttle response in the house error shape with `Retry-After`, and no
  mutation side effects when a request is refused.
- Circuit breakers only around outbound calls whose failure is not the whole
  monolith: relay → Kafka, storage → MinIO, notifications → Resend/Slack.
  PostgreSQL, Redis and ClickHouse stay unwrapped.
- Fail-fast behavior (503 `dependency_unavailable` on HTTP, fast failure into
  existing worker retry/backoff policies) without hangs.
- Bounded metrics labels under ADR 0023: `dependency` ∈
  {`kafka`,`minio`,`resend`,`slack`}, `tier` ∈ {`auth`,`mutation`}; no
  tenant/user/employee labels.
- A load gate that is a command, wired into `make check`, with thresholds as
  code: 25 check-ins/s for 60s plus board reads, p95 < 300 ms and zero errors.

Alternatives considered:

- **Per-router rate-limit dependencies.** Rejected: a new route or router can
  silently forget the dependency; the spec's "covered by default" rule wins.
- **A token bucket in process memory.** Rejected: the API is a separate
  process from workers and may scale out; counters must be shared. Redis is
  already a dependency (realtime fan-out).
- **A fixed-window Redis counter.** Rejected: it admits a 2× burst at window
  boundaries. The exact sorted-set window costs one Lua script call per
  mutation and gives a correct reset instant for `Retry-After`.
- **A framework breaker library.** Rejected for one small deterministic state
  machine with an injectable clock and no extra dependency.
- **Wrapping PostgreSQL/Redis/ClickHouse.** Rejected: in this monolith their
  failure is total, so a breaker only adds latency to a dead path.
- **Disabling the limiter for the load run.** Rejected by the headroom rule; the
  load fixtures must be designed so the shipped defaults pass unchanged.

## Decision

### Rate limiting

1. **One middleware, two tiers.** `RateLimitMiddleware` in
   `app/shared/rate_limit.py` runs inside telemetry/logging and before any
   router or service work. Tiers:

   | Tier | Routes | Redis key | Default |
   | --- | --- | --- | --- |
   | `auth` | `POST /api/v1/auth/login`, `PUT /api/v1/auth/password` | client IP (first trusted `X-Forwarded-For` hop, else the socket peer) | 30 req / 60s |
   | `mutation` | every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` (including logout) | session user id, client-IP fallback when no session resolves | 120 req / 60s |
   | exempt | all `GET`, `/healthz`, `/metrics` | — | — |

   Keys are Redis key components only — never logged and never metric labels.
   `X-Forwarded-For` is client-controlled, so it is honored only when the
   immediate peer is listed in `RATE_LIMIT_TRUSTED_PROXIES` (the proxy that
   overwrites the header). The default empty list keys the auth tier on the
   socket peer; spoofing the header cannot rotate around the login budget.

2. **Exact sliding window in one Lua script.** `ZREMRANGEBYSCORE` →
   `ZCARD` → reject-or-`ZADD`/`PEXPIRE` executes atomically, so concurrent
   requests cannot race a read-then-write burst past the budget. A rejected
   request appends nothing; `Retry-After` is `ceil(oldest_score + window − now)`
   with a one-second floor. `clock` is injectable (`time.time` by default, so
   window scores compare across processes); tests never sleep on the wall clock.

3. **Fail-open.** Redis errors/timeouts are swallowed after a PII-free warning
   and the `workpulse_rate_limit_fail_open{tier}` counter; the request proceeds.
   The existing Redis client timeouts (0.5s connect / 1s socket) bound the wait.
   Reliability machinery never turns user traffic into an outage.

4. **Refusals are side-effect free.** The middleware renders
   `429 {"error":{"code":"rate_limited",...}}` with `Retry-After` directly
   through the shared error helper — user middleware runs outside Starlette's
   exception middleware, so raising would become a 500. No router, service,
   audit entry or outbox event runs on a rejection. Authorization is untouched:
   a request under budget still answers its own 403/404.

### Circuit breakers

5. **Per-process state machine, placed at adapters.** `app/shared/circuit_breaker.py`
   is closed → open after 5 consecutive failures, fails fast with no outbound
   I/O while open, and after a 30s cooldown enters half-open admitting exactly
   one trial call (concurrent callers fail fast). Success closes and resets
   the streak; failure re-opens with a fresh cooldown. Threshold/cooldown are
   `CIRCUIT_BREAKER_*` Settings. State is in memory, matching the v1 deployment
   (one uvicorn process, one process per worker); multi-process deployments
   would need shared state and are out of scope.

6. **Only non-critical outbound dependencies are wrapped.** The relay's Kafka
   publish, `MinioDocumentStorage`, and the Resend/Slack send adapters. A
   `CircuitOpenError` subclasses `DependencyUnavailableError`, so HTTP surfaces
   answer `503 dependency_unavailable` through the normal handler; worker units
   fail fast into their existing policies (ADR 0015 bounded retries → terminal
   `failed`; relay bookkeeping/backoff with rows unpublished). Domains never
   import the breaker.

7. **MinIO gains bounded call timeouts.** The breaker cannot make one hung
   call fail fast, so the storage client pins connect/read timeouts
   (`STORAGE_CONNECT_TIMEOUT_SECONDS=3`, `STORAGE_READ_TIMEOUT_SECONDS=5`, one
   botocore attempt). These are sized for a 20 MB document HEAD/copy/delete
   and stay well below the breaker cooldown.

8. **Metrics under ADR 0023.** `workpulse_circuit_breaker_state` (gauge,
   one-hot by `dependency`/`state`), `workpulse_circuit_breaker_rejected`
   (`dependency`), `workpulse_circuit_breaker_transitions`
   (`dependency`/`state`), `workpulse_rate_limit_throttled` (`tier`) and
   `workpulse_rate_limit_fail_open` (`tier`). No unbounded labels.

9. **A 2xx means committed.** The load gate surfaced FastAPI's default
   request-scope exit for yield dependencies: the response could be sent before
   `get_session` committed, so a client that immediately read its own write
   (check-in → check-out) could see a 404. `SessionDep` now uses
   `scope="function"`, so commit/rollback completes before the response is
   sent. This is a correctness rule, not a load-test workaround. The floor is
   pinned at `fastapi>=0.121.1` in `apps/api/pyproject.toml`.

### Load gate

10. **k6 smoke as a command.** `make check-load` runs
    `tests/performance/run.sh`: it provisions deterministic synthetic users
    and one opaque session per fixture (raw tokens in a 0600 temp file), starts
    a private API on `:8010`, runs `tests/performance/smoke.js` for 60s at a
    sustained 25 check-ins/s plus one manager board read per second, and
    removes its synthetic facts on exit. The profile never logs in, so the
    auth tier is exercised by its own integration tests rather than by the
    load gate. It joins `make check` like `check-e2e`; a missing `k6` binary
    fails loudly rather than skipping the SLO.

11. **Fixtures give the limiter ≥2× headroom.** 50 fixed synthetic employees
    (`load-01` manager of the other 49) each link to a pre-provisioned session.
    Each iteration uses `exec.scenario.iterationInTest` to round-robin users,
    so per-user mutation budgets are used evenly: 1,500 pairs/minute × 2 =
    3,000 requests against 50 × 120 = 6,000 per minute. Fixture passwords are
    random throwaway values (no known credential) and the script refuses to
    run outside development/test unless explicitly overridden. Fixture cleanup
    removes the run's attendance, sessions and outbox rows so `make check-e2e`
    keeps its clean-outbox precondition.

12. **Thresholds are code.** `http_req_failed{scenario:smoke} == 0` (the
    check-in request marks its 409 recovery response as expected, so only the
    final 201 counts),
    `http_req_duration{scenario:smoke} p95 < 300`, `checks == 1` and
    `dropped_iterations == 0`. Any 429/503 is a non-2xx and fails the gate by
    construction; a threshold breach fails the command.

## Consequences

- The limiter is exact (no boundary bursts) at the cost of one Redis Lua call
  per throttled-tier request; fail-open keeps Redis from becoming a new SPOF.
- Mutation identity resolution adds one session lookup per mutation. It is
  bounded by the mutation budget and reuses the auth service seam; router
  authorization still makes the only access decision.
- Sessionless logout is keyed by IP so it stays bounded; budgets are
  per-user otherwise, hence tenant-scoped by construction.
- Breaker state is per process. A rolling deploy resets it; a multi-process
  deployment would need a shared state store (out of scope).
- The load gate pins k6 as a local/CI prerequisite. If CI cannot install k6,
  the gate's placement in `make check` needs a PO revisit (recorded in the
  feature spec's risks).
- `scope="function"` makes response latency include the commit, which is the
  honest measure of a durable write; the load gate's p95 now covers it.
- Web surfaces render the pinned `common.errors.rateLimited` /
  `common.errors.dependencyUnavailable` copy in en + vi for 429/503 before any
  feature fallback; raw server text never reaches those states.
- This ADR covers only the v1/local single-process deployment. Shared breaker
  state, per-tenant quotas and edge/DDoS limits remain out of scope.
