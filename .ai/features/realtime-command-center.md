# Feature: Realtime Command Center

- **Status:** done
- **Phase:** 2
- **Size:** M
- **Surfaces:** api+web
- **Links:** ADR 0002 (modular monolith) · ADR 0006 (Phase-2 pre-agreed:
  `redis` redis-py async + FastAPI-native WebSockets) · ADR 0009 (board /
  lateness semantics — the presence rules this feature projects) · ADR 0010
  (session-resolved identity) · `.ai/domain.md` (Realtime Command Center
  context, PresenceState) · `.ai/features/attendance.md` (the source of presence
  facts) · `.ai/features/identity-access.md` (sessions) · `.ai/product.md`
  goal 1 + Phase-2 metric ("check-in visible on the manager board ≤ 2s, p95")

## Problem

Phase 1's workforce board is request/response: a manager learns who checked in
when they next refresh the page. The product's core promise is the opposite —
"a check-in reaches the manager's board within seconds" — and the architecture
names Redis as the store that answers "what is true right now". The Realtime
Command Center is that live wall: a dedicated page where managers and HR watch
presence arrive in real time (who is working, who is late, who is out), fed by
a WebSocket, with Redis holding the hot PresenceState and PostgreSQL remaining
the source of truth.

## Business rules

- **PostgreSQL is the source of truth; PresenceState is a live derived
  projection.** It is materialized as a per-connection projection (rows map +
  recounted counts) rebuilt from a PostgreSQL snapshot at connect time and
  kept fresh by channel frames — **no Redis rows cache exists** (explicitly
  deferred; a read-through cache with TTL is the known upgrade, one port
  swap). A Redis outage degrades latency — deltas stop, the client's ~60s
  snapshot refresh keeps the board current — and never invents facts.
  Domains never import Redis directly — a port wraps it
  (`.ai/architecture.md` rule 3).
- **Fan-out crosses API processes through Redis pub/sub**, one channel per
  tenant (`realtime:tenant:{tenant_id}`). An in-process bus is forbidden:
  deployments with several API workers must all reach every viewer.
- **Delivery is transient, at-most-once** (decision 3). Pushes are full-row
  idempotent upserts, so a duplicate is harmless; missed updates are recovered
  by resyncing a snapshot on reconnect — durable replay is Phase 3's
  transactional outbox + Kafka. When that lands, the realtime consumer feeds
  this same projection and protocol from `attendance.*` events with no protocol
  change.
- **Presence semantics are exactly `GET /attendance/board`'s (ADR 0009):**
  workday = the calendar date of `checked_in_at` in the employee's location
  timezone (kept across midnight); `late` is set once at check-in against the
  exclusive `shift.start + grace` boundary and never cleared; counts **overlap,
  they are not a partition** (`late` can co-occur with `present`/`remote`);
  `on_leave` is always `0` until the Leave context lands; `absent` counts active
  in-scope employees with an expected shift (location's, else tenant default),
  no record at all for the workday, and `now > start + grace`; rest-day
  over-count is accepted and documented in UI copy (ADR 0009 Decision #7).
- **PresenceState row = one status plus an orthogonal `late` flag** (ADR 0009:
  "late is orthogonal to presence, remote is a work mode"): `status ∈
  present | remote | checked_out | absent | on_leave`, `late: bool`,
  `minutes_late: int | null`. `.ai/domain.md`'s ubiquitous-language entry lists
  `LATE` as a status — that one-line wording fix ships with this feature.
- **Presence rows cover** every in-scope employee with a record for the
  workday, plus every in-scope employee currently counted `absent`. An
  employee with an expected shift before `start + grace` has no row yet —
  identical to the board, which counts them nowhere.
- **Current workday only** (decision 5). No date parameter anywhere; historical
  review stays on the attendance records page. A delta whose `date` differs
  from the client's current snapshot (e.g. a 23:58 check-in whose workday
  rolled) makes the client refetch its snapshot.
- **Clock-driven transitions push nothing** (decision 6): the server pushes
  only event-driven deltas (check-in / check-out); the client refreshes its
  snapshot about every 60s, so `absent` onset at `start + grace` appears with
  at most a minute of lag. The ≤2s metric applies to check-in visibility.
- **Scope mirrors `GET /attendance/board`** (decision 2): `manager` sees self +
  direct reports; `hr`/`admin`/`super_admin` see the whole tenant;
  `employee`/`finance` are denied. Scope is recomputed on every snapshot, so
  team changes apply within one refresh.
- **Frames and snapshots carry identifiers and state only** — never names,
  emails, notes or any PII (`.ai/security.md`). The web joins display names
  from employee data it already holds. Same rule as event payloads.
- **Identity is session-resolved** (ADR 0010): the WS handshake and the REST
  snapshot resolve `Identity` from the `wp_session` cookie through
  `app/dependencies.py`, exactly like every other route. A missing/invalid
  session is rejected before any board frame is sent (401); insufficient role
  is rejected the same way (403).
- **The surface is read-only.** No mutation exists here, so no audit entry is
  written; the denied-role and tenant-isolation tests still apply.
- New dependencies are the pre-agreed Phase-2 set only: `redis` (redis-py
  async) + FastAPI-native WebSockets (ADR 0006) — rule 10 satisfied.

## Scope

- `WS /api/v1/realtime/board` — the live stream (handshake auth, snapshot as
  first frame, then `presence.upsert` deltas).
- `GET /api/v1/realtime/board` — REST snapshot, same payload as the `snapshot`
  frame: initial render and degraded mode (decision 7).
- The realtime domain: PresenceState projection (same rules as the attendance
  board), the WS hub, the Redis pub/sub publisher + subscriber, and the
  presence-delta port that Attendance calls after check-in/check-out.
- Web: `features/realtime/` + a `(dashboard)/command-center/` page — live
  counts, a who-is-working-now table (names joined client-side) and a live
  activity rail — with loading, empty, "live updates unavailable" and denied
  states.
- Infra: `redis` + `redis-test` services in `docker-compose.yml`, a Redis
  client wrapper in `app/infrastructure/redis/`, and `Settings.redis_url` in
  `apps/api/app/config.py` (default `redis://localhost:6379/0` — the repo has
  no `.env.example`; the settings default carries it).
- Contract: versioned WS frame schemas in `contracts/realtime/`.
- Tests: unit (projection, injected clock), integration (REST snapshot, hub
  fan-out against Redis), contract (frames vs schema, PII absence), e2e (live
  check-in → wall updates; degraded/reconnect paths).

## Out of scope

- **Employee lifecycle pushes** (decision 4): created / transferred / terminated
  changes appear on the next snapshot refresh; Phase 3 events can push them
  later.
- **History / date navigation** (decision 5) — attendance records page owns it.
- **Durable replay, missed-delta catch-up, exactly-once delivery** — Phase 3
  outbox + Kafka (decision 3). Redis Streams is explicitly not adopted.
- **`on_leave` population** — always `0`/absent-of-rows until the Leave context
  lands; the fields stay so the contract never changes.
- New domain events: `contracts/events/` gains nothing — the stream is a
  projection of `attendance.*` facts, not a new fact source.
- Notifications / escalation ("someone is late" alerts) — Phase 3.
- Any mutation from the board (check-in/out actions stay on the attendance
  page).
- Wall-display/TV layouts, multi-board views, maps, presence of *users*
  (online users) rather than employees.
- Login rate limiting / connection-count quotas (Phase 7).

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/realtime/` (projection, hub, ports) |
| API router | `apps/api/app/api/v1/realtime.py` (REST snapshot + WS upgrade on `/realtime/board`) |
| Infra | `apps/api/app/infrastructure/redis/` (pub/sub publisher/subscriber, PresenceState store) |
| Contracts | `contracts/realtime/board.v1.json` (WS frame + snapshot schemas) |
| Workers | — (Phase 2 keeps the hub inside `apps/api`; `workers/` arrives with Phase 3) |
| Web feature | `apps/web/features/realtime/` |
| Web routes | `apps/web/app/(dashboard)/command-center/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/command-center.spec.ts` |
| Infra config | `docker-compose.yml` (redis + redis-test services) · `apps/api/app/config.py` (`Settings.redis_url`; no `.env.example` exists — the default lives here) |

Touch points in existing code (same change; their specs updated with the why):
`attendance/service.py` calls the realtime presence-delta port after check-in /
check-out; `app/dependencies.py` reuses `get_identity` for the WS handshake.

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /api/v1/realtime/board` (REST snapshot) | — | team (self + reports) | Y | — | Y | Y |
| `WS /api/v1/realtime/board` (live stream) | — | team (self + reports) | Y | — | Y | Y |
| `/command-center` page | hidden + denied | Y | Y | hidden + denied | Y | Y |

Cross-tenant: a connection only ever subscribes to its session tenant's
channel; cross-tenant data returns `404`/empty, never another tenant's rows.

## API contract (pinned 2026-10-01)

| Method & path | Input | Response |
| --- | --- | --- |
| `GET /api/v1/realtime/board` | — (current workday only) | `200 RealtimeBoardSnapshot` |
| `WS /api/v1/realtime/board` | — (cookie handshake; no client frames in v1) | `snapshot` frame, then `presence.upsert` frames |

```jsonc
// REST 200 and the WS "snapshot" frame share one payload:
RealtimeBoardSnapshot {
  "date": "YYYY-MM-DD",                       // current workday (server UTC date)
  "counts": { "present", "remote", "late", "on_leave", "absent", "checked_out" },  // overlapping, ADR 0009
  "presence": [ PresenceRow ],
  "activity": [ { "occurred_at": date-time, "employee_id": uuid,
                  "action": "checked_in|checked_out" } ]   // newest first, capped 20
}

PresenceRow {
  "employee_id": uuid,
  "status": "present|remote|checked_out|absent|on_leave",
  "late": bool,
  "minutes_late": int|null,                   // set with late at check-in; else null
  "since": date-time|null,                    // checked_in_at (present/remote) | checked_out_at (checked_out) | null
  "attendance_id": uuid|null                  // null for absent rows
}

// WS frames (server → client)
{ "type": "snapshot", ...RealtimeBoardSnapshot }        // first frame after handshake
{ "type": "presence.upsert",
  "date": "YYYY-MM-DD", "counts": { ... },              // full counts every delta — no client-side derivation
  "row": PresenceRow,
  "occurred_at": date-time,
  "reason": "checked_in|checked_out" }                  // drives the activity rail

// WS frames (client → server): none in v1 (protocol ping/pong only)
```

### Pinned details

- **Routes & operationIds** (drive orval hook names): `getRealtimeBoard`
  (`GET /realtime/board` → `200 RealtimeBoardSnapshot`). The WS share the
  resource path `/api/v1/realtime/board` as an upgrade — one resource, two
  transports, one payload shape. Frames are validated against
  `contracts/realtime/board.v1.json`, not OpenAPI.
- **Handshake auth** (mechanics verified in T1, 2026-10-01): `wp_session`
  cookie resolved via `app/dependencies.py` (ADR 0010). Denial is
  **close-before-accept** — `websocket.close` before `websocket.accept`, zero
  application frames — and uvicorn's WS implementations answer that with one
  upgrade status, **403**, for both cases (a missing/invalid session and a
  valid session with an insufficient role look identical on the upgrade).
  401-vs-403 is deliberately not carried on the upgrade: the ASGI
  `websocket.http.response` extension could express distinct statuses but is
  not used, and browser JS could not observe them anyway (the WebSocket API
  surfaces only error/close). The distinction lives on the REST snapshot,
  which uses the same resolution and the uniform error shape: missing/invalid
  session → `401 unauthenticated`; valid session, insufficient role → `403
  forbidden`.
- **Delta contract**: `presence.upsert` is a **full-row idempotent upsert** with
  fresh `counts` — redelivery is harmless and clients never patch partial
  state. At most one delta per attendance transition (a late check-in carries
  `late: true` and `minutes_late` in the row; there is no separate late frame).
- **`minutes_late` recompute**: the `late` flag is stored and immutable;
  `minutes_late` is recomputed from the record's `shift_id` + the shift's
  `start_time`/`grace_minutes` (the shared `_shift_boundary`) at read/publish
  time. Editing a shift after a late check-in therefore changes the displayed
  minutes — accepted (risk 3); the `late` flag itself never drifts.
- **Snapshot recomputation** always follows ADR 0009's board rules (workday in
  the location timezone, shift resolution location → tenant default → none,
  exclusive lateness boundary) — the projection and `GET /attendance/board`
  must never disagree; a contract/integration test asserts they match.
- **Presence row set** is per business rules: records for the workday ∪
  currently-absent employees, restricted to the identity's visibility scope;
  rows are unordered (the client sorts), `presence` is uncapped,
  `activity` is newest-first capped 20 like the attendance board.
- **`date` rollover**: a delta whose `date` ≠ the client's snapshot date does
  not patch rows — the client refetches `GET /realtime/board`. Server-side,
  midnight rollover is handled by snapshot recomputation (UTC date).
- **Heartbeat**: uvicorn's built-in WebSocket ping/pong
  (`uvicorn[standard]`); dead connections are reaped. No application-level
  `ping`/`pong` frames and no lifespan task — each connection subscribes to
  `realtime:tenant:{tenant_id}` itself on upgrade (a refcounted in-process hub
  is the known upgrade path for N viewers; the protocol does not change).
- **Error codes** (REST, uniform shape): `unauthenticated` 401 · `forbidden`
  403 · `validation_error` 422 (uniform handler, unused — the snapshot takes no
  parameters). WS rejections are HTTP status on the upgrade response, not the
  JSON error shape.
- **Ids/time**: UUIDv7; timestamps UTC ISO 8601 (`timestamptz` semantics);
  `now` is injected (conventions) and the only wall-clock read — late/absent
  judgements never call `datetime.now()`.

## Events

**None new.** The stream is a projection of the Phase-1 facts
`attendance.checked_in` / `attendance.checked_out` / `attendance.late`
(contracts already in `contracts/events/attendance/`), built with the same
rules as the attendance board. WS frames live in `contracts/realtime/` because
they are projections, not domain facts — they must not join the event catalog.
When the Phase 3 outbox + Kafka land, the realtime consumer replaces the
in-process port as the projection's input; the frame protocol is unchanged.

## Edge cases

- Redis unreachable → REST snapshot still serves from PostgreSQL (truth);
  live deltas stop; the client's ~60s snapshot refresh keeps the board current
  and the "live updates unavailable" state shows. No invented presence.
- Server restart / process count change → all sockets drop, clients reconnect
  and resync from a fresh snapshot; multi-worker fan-out still works because
  the bus is Redis, not process memory.
- Check-in at 23:58, check-out at 00:10 → one workday (ADR 0009); if the
  wall's snapshot `date` differs from the delta's `date`, the client refetches
  instead of patching.
- Absent onset at `start + grace` with no event → appears only at the next
  ~60s snapshot refresh (decision 6; documented in UI copy if it reads as a
  lag).
- Employee transferred between managers mid-session → their row leaves/joins
  each manager's scope at the next snapshot; deltas to a now-out-of-scope
  connection are filtered server-side and dropped.
- Terminated employee with a record left open (ADR 0009 accepted) → the row
  keeps showing the open record until it closes; termination itself is not
  pushed (decision 4).
- No shift configured → nobody is ever `absent`, `late` stays `false` — same
  as the board.
- Empty tenant / no attendance yet → counts all zero, `presence` empty; the
  page renders its empty state.
- `employee`/`finance` opens the page or socket → nav hidden, page denied,
  upgrade 403 — no frame ever leaks.
- Two tabs checking in concurrently → one record, one delta (the attendance
  partial-unique guarantee upstream).

## Acceptance criteria

- [x] API: given an `hr` session, when `GET /api/v1/realtime/board` is called,
  then `200` returns `date` = current workday with overlapping counts and
  presence rows that exactly match `GET /attendance/board` semantics (ADR 0009)
  for the same instant.
- [x] API (live): given a manager's open WS on `/api/v1/realtime/board`, when a
  report checks in, then the socket receives one `presence.upsert` with the
  full row, fresh `counts` and `reason: "checked_in"` — visible on the wall
  within 2 seconds of the check-in response (the Phase-2 metric).
- [x] API (failure): given an `employee` or `finance` session, when they request
  the snapshot or attempt the upgrade, then `403` (uniform `forbidden`) /
  rejected upgrade, and no board frame is ever delivered.
- [x] API (failure): given a missing or invalid `wp_session` cookie, then the
  REST snapshot is `401` (`unauthenticated`) and the upgrade is rejected
  before any frame (single denial status on the upgrade — Pinned details).
- [x] API (tenant isolation): given sockets in two tenants, when tenant A
  checks in, then tenant B receives no frame, and each snapshot contains only
  its own tenant's rows (404/empty rules).
- [x] API (failure): given Redis is unreachable, when the snapshot is requested,
  then it is still correct from PostgreSQL and no presence state is invented.
- [x] Contracts: every produced frame validates against
  `contracts/realtime/board.v1.json`, and a test asserts no name/email/note or
  other PII field exists in any frame or snapshot.
- [x] Unit: the projection (workday, late, minutes_late, absent onset) uses an
  injected clock — no wall-time assertions.
- [x] Web: given a manager opens `/command-center`, then live counts, the
  who-is-working rows (names joined from employee data) and the activity rail
  render from the snapshot, with loading and empty states.
- [x] Web (failure): given the WS cannot connect, then the page renders the REST
  snapshot with a visible "live updates unavailable" state and refreshes the
  snapshot about every 60s.
- [x] Web (denied): given an `employee`/`finance` login, then no command-center
  nav is shown and direct navigation renders the denied state.
- [x] E2E: an employee checks in while a manager watches `/command-center`, then
  the row flips to `present` (with `late` per the shift) without a reload,
  within 2 seconds.
- [x] E2E (edge): after the socket drops and reconnects, the wall resyncs from a
  fresh snapshot — a change made while disconnected is visible after reconnect.

## Decisions (PO, 2026-10-01)

1. **Placement: a new `/command-center` page.** The attendance page keeps its
   board/records/shifts as-is; the command center is a dedicated live wall
   (counts + who-is-working rows + activity rail). Rationale: the wall is a
   different consumption mode (continuous watching) from records work, and
   Realtime is its own bounded context.
2. **Access mirrors `GET /attendance/board`.** Manager = self + direct reports;
   HR/Admin/Super Admin = whole tenant; Employee/Finance denied (`403` /
   rejected upgrade). Rationale: same capability row ("View team attendance &
   profiles") already governs this data.
3. **Delivery: transient Redis pub/sub** (at-most-once), snapshot on connect,
   resync on reconnect. Rationale: boring first — durability and replay belong
   to Phase 3's outbox + Kafka; Redis Streams would duplicate that machinery.
4. **Content: attendance presence deltas only** (check-in / check-out / late
   judgement carried with the check-in row) + initial snapshot. Employee
   lifecycle changes appear on snapshot refresh; Phase 3 events can push them.
5. **Time range: the current workday only.** No date navigation; historical
   review stays on the attendance records page.
6. **Absent onset: slow snapshot refresh.** Event-driven changes arrive in
   seconds; clock-driven `absent` transitions appear within the ~60s client
   refresh rather than via server timers.
7. **Fallback: a REST snapshot endpoint** — `GET /api/v1/realtime/board`
   returns the exact `snapshot` payload, used for initial render and degraded
   mode with a visible "live updates unavailable" state.

## Plan

### Acceptance criteria → where they are satisfied

| # | Acceptance criterion | Satisfied by |
| --- | --- | --- |
| 1 | REST snapshot matches `GET /attendance/board` semantics | T3 (`board_detail` shares `board()`'s code) + T4 (consistency test) |
| 2 | Live `presence.upsert` ≤2s after check-in | T2 (bus) + T3 (publish hook) + T4 (stream) + T6 (e2e timing) |
| 3 | employee/finance → 403 / upgrade rejected, zero frames | T4 (REST + WS tests), T6 (e2e denied) |
| 4 | Missing/invalid cookie → 401, upgrade rejected before frames | T4 (REST 401 + WS denial tests), T6 (cookie-less WS) |
| 5 | Tenant isolation (no cross-tenant frames/rows) | T2 (channel-per-tenant test) + T4 (snapshot + stream tests) |
| 6 | Redis down → snapshot still correct from PostgreSQL | T2 (best-effort publish) + T3/T4 (snapshot path touches no Redis) |
| 7 | Frames validate against `contracts/realtime/board.v1.json`, no PII | T1 (schemas) + T4 (contract tests) |
| 8 | Unit tests use an injected clock | T3 + T4 unit tests |
| 9 | Web renders snapshot (counts, rows with joined names, rail) + loading/empty | T5 |
| 10 | Web: WS unreachable → REST snapshot + "live updates unavailable" + ~60s refresh | T5 |
| 11 | Web: employee/finance → hidden nav + denied page state | T5 (+ `destinations.ts` roles) |
| 12 | E2E: check-in flips the wall without reload, within 2s | T6 |
| 13 | E2E: drop + reconnect resyncs from a fresh snapshot | T6 |

### Design pins (fixed by the research of 2026-10-01)

- **One shared projection.** `AttendanceService.board()` is refactored into
  `board_detail()` (counts + per-employee rows + activity) with `board()` as a
  thin wire mapping — the realtime snapshot and the attendance board can never
  drift because they are one code path (criterion 1).
- **PresenceState = live per-connection projection** (rows map + recount),
  fed by per-tenant Redis pub/sub; **no Redis rows cache** — snapshot always
  recomputes from PostgreSQL (criterion 6 holds trivially; cache deferred,
  see Risks). Business rule 1's wording is adjusted in T1 accordingly.
- **Frames carry the changed row + recounted `counts` over the connection's
  known row set** (snapshot rows ∪ accepted upserts) — server-side derivation,
  never client-side. Clock-driven absent onset is not pushed (decision 6).
- **`minutes_late` is recomputed** from `record.shift_id` + `_shift_boundary`
  at read/publish time (the `late` flag itself is stored and immutable).
- **Scope**: reuse `_scoped_employees` exactly (research confirmed: manager →
  self + direct reports; unlinked manager → nobody; others → tenant).
  Out-of-scope facts are dropped server-side after a lazy visible-set refresh,
  bounded to one refresh per ~60s event-time window per connection so a burst
  of foreign facts never costs one scope query per fact.
- **No request-scoped DB session on a socket**: the WS handshake and stream
  read through short-lived sessions (one per snapshot/scope read), so a viewer
  parked on Redis never pins a pooled PostgreSQL connection — the REST routes
  keep their normal request-scoped session.
- **WS denial**: close-before-accept (zero application frames); both denial
  cases surface as the single ASGI denial status **403** — verified in T1:
  uvicorn hardcodes 403 on the pre-accept close path in both WS
  implementations. 401 vs 403 is expressible via the ASGI
  `websocket.http.response` extension (Starlette 1.7's `WebSocket.send`
  supports it) but is not used; the distinction is carried by the REST codes.
- **Heartbeat** = uvicorn's built-in WS ping/pong (`uvicorn[standard]`); no
  application-level ping frames. No lifespan task needed: each connection
  subscribes to the tenant channel itself (upgrade path: refcounted hub).
- **No Postgres migration, no new domain events, no audit entries** (read-only
  surface). Redis enters exactly where ADR 0006 phase-gated it.

### T1 — Pin the WS protocol contracts + remaining technical details (docs only) · done 2026-10-01

**Changes**

- `contracts/realtime/board.v1.json` — Draft 2020-12 JSON Schema (same dialect
  as `contracts/events/attendance/*.v1.json`, `additionalProperties: false`):
  `presence-row`, `board-snapshot`, and a `frame` union discriminated on
  `type: "snapshot" | "presence.upsert"` — exactly the shapes pinned in API
  contract above. Contract before producer/consumer. The frame union is the
  root (WS messages validate against the file); REST bodies validate against
  `#/$defs/board-snapshot`.
- `contracts/README.md` — one paragraph: `contracts/realtime/` holds versioned
  WebSocket **projection** protocols (not the event catalog; same
  additive-only / new-version-on-break rule; no PII).
- Spec updates in this same change (pin-remaining-details precedent =
  identity-access T1):
  - Handshake denial mechanics: verified the installed Starlette 1.7.0 +
    uvicorn close-before-accept path — it answers a single **403** for both
    denial cases (both WS implementations hardcode it), 401-vs-403 is only
    expressible via the unused ASGI `websocket.http.response` extension and
    would be invisible to browser JS; the distinction is observable via REST
    `GET /realtime/board` and pinned there.
  - PresenceState materialization wording (business rule 1): live projection +
    channel frames; Redis rows cache explicitly deferred.
  - `minutes_late` recompute rule (from `record.shift_id` at read time).
  - Heartbeat = uvicorn built-in; subscription model = per-connection on
    `realtime:tenant:{tenant_id}`.
  - Surfaces & files fix: the repo has no `.env.example` — `Settings.redis_url`
    carries the default instead.
- `.ai/domain.md` ubiquitous language: Presence state loses `LATE` from the
  status enum ("…with an orthogonal `late` flag, ADR 0009") — the one-line
  wording fix business rule 5 commits to.

**Verify:** `make check` green (docs + inert JSON only; research confirmed
nothing globs `contracts/` outside `contracts/events/` and `openapi.json`).
The schema itself is checked as valid Draft 2020-12 and smoked against
accept/reject frame samples (extra keys, PII keys, bad discriminator, caps).

### T2 — Redis infrastructure (deps · compose · settings · ports · adapters) · done 2026-10-01

**Changes**

- `uv add redis` in `apps/api` (justified: this spec; pre-agreed ADR 0006
  phase gate — repo rule 10 satisfied).
- `docker-compose.yml`: `redis` (redis:7-alpine, `6379:6379`, healthcheck) and
  `redis-test` (`6380:6379`, healthcheck) — mirrors `postgres`/`postgres-test`.
- `Makefile`: `redis-up` / `redis-test-up` targets
  (`docker compose up -d --wait …`); `check-api: db-test-up redis-test-up`;
  `check-e2e: db-up redis-up db-migrate db-seed`.
- `apps/api/app/config.py`: `redis_url: str = "redis://localhost:6379/0"`.
- `apps/api/app/shared/presence.py` — the port trio (copy the
  `shared/audit.py` precedent): `PresenceFact` (pydantic: `tenant_id`,
  `workday`, `employee_id`, `attendance_id`, `status: "present|remote|checked_out"`,
  `late: bool`, `minutes_late: int | None`, `since: datetime`,
  `reason: "checked_in|checked_out"`, `occurred_at`) ·
  `PresencePublisher(Protocol)` (`async publish(fact) -> None`) ·
  `PresenceStream(Protocol)` (`subscribe(tenant_id) -> AsyncIterator[PresenceFact]`).
- `apps/api/app/infrastructure/redis/`:
  `client.py` (lazy `get_redis()` / `dispose_redis()` — mirror
  `infrastructure/postgres/engine.py`) · `presence.py`
  (`RedisPresencePublisher`: JSON `PUBLISH` to `realtime:tenant:{tenant_id}`,
  Redis errors logged with ids only and **swallowed** — a check-in never fails
  because Redis is down) · `streams.py` (`RedisPresenceStream`: `pubsub`
  subscribe adapter).
- `apps/api/tests/conftest.py`: set `REDIS_URL` to the `redis-test` instance
  beside the `DATABASE_URL` swap.
- `apps/api/tests/integration/test_realtime_bus.py`: publish→subscribe
  round-trip on `redis-test`; two tenants never cross channels; publisher
  against a dead Redis endpoint does not raise.

**Verify:** `make check-api` (ruff/pyright/pytest) green with the new bus
tests; `make check` unchanged elsewhere. No web impact.

### T3 — Attendance: shared board projection + presence publish hook · done 2026-10-01

**Changes** (attendance domain only + wiring)

- `apps/api/app/domains/attendance/service.py`:
  - Extract the row projection helper (`to_presence_row(record, shifts)` →
    status from open/closed + `work_mode`, `late` from the record,
    `minutes_late` recomputed via `_shift_boundary` when late and
    `record.shift_id` resolves, `since` per the wire rule) — one function
    shared by snapshot rows and delta facts.
  - `board_detail(identity, board_date, now) -> BoardDetail` (date, counts,
    rows — the `_absent_count` loop becomes `_absent_rows` and
    `absent = len(rows)`, so rows and counts cannot disagree — plus activity).
    `board()` becomes a thin mapping to the existing `Board` schema (wire
    shape unchanged; every existing attendance test must pass untouched).
  - Constructor gains `presence: PresencePublisher`. `check_in` (after
    `_repo.add`, alongside `produced`) and `check_out` (after `_repo.close`)
    build a `PresenceFact` — `check_in` reuses the freshly computed
    `minutes_late`; `check_out` recomputes via the helper so a late flag's
    minutes survive the row replace — and call
    `await self._presence.publish(fact)` best-effort (failure logged, never
    raised).
- `apps/api/app/dependencies.py`: `get_attendance_service` wires
  `presence=RedisPresencePublisher(get_redis())`.
- Tests:
  - `tests/unit/`: fact contents for on-time check-in, late check-in
    (`minutes_late`), check-out (minutes preserved; injected clock only) —
    recording fake; a raising fake proves check-in/check-out still succeed and
    still return their `AttendanceMutation`.
  - `tests/integration/test_attendance_api.py` (additions): `board()` output
    unchanged; `counts == recount(rows)` and rows ⊇ every record for the date
    + every absent employee (the projection invariants).

**Verify:** `make check-api` green — including the untouched existing
attendance suite (the refactor is behavior-preserving).

### T4 — Realtime domain + REST snapshot + WS endpoint + contract tests + regen · done 2026-10-01

**Changes**

- `apps/api/app/domains/realtime/`:
  `models.py` (`PresenceState` dataclass — the connection-scoped hot state:
  workday, rows map, counts; no ORM) · `schemas.py` (wire `PresenceStatus`,
  `PresenceRow`, `RealtimeBoardSnapshot`, frame models — names pin the orval
  type names) · `policies.py` (`ensure_can_read_presence`, BOARD_ROLES mirror
  of attendance's board policy — domains don't import each other's internals)
  · `service.py` (`RealtimeService`: `snapshot(identity, now)` maps
  `board_detail` → wire; `stream(identity, now)` yields the `snapshot` frame
  first, then `async for fact in bus.subscribe(tenant_id)` — in-scope fact →
  row upsert + recount → `presence.upsert` frame; out-of-scope → lazy
  visible-set refresh once, re-check, else drop; idempotent redelivery is
  harmless by construction).
- `apps/api/app/api/v1/realtime.py`:
  `GET /realtime/board` (`operation_id="getRealtimeBoard"`,
  response `RealtimeBoardSnapshot`, `_now()` pattern, policy guard) and
  `@router.websocket("/realtime/board")` (identity dependency → policy →
  close-before-accept on denial → `async for frame in service.stream(...):
  await ws.send_text(frame)` with a concurrent receive to detect disconnect).
  Register in `app/api/v1/__init__.py`; `RealtimeServiceDep` in
  `dependencies.py`.
- `make gen-api` → `contracts/openapi/openapi.json` +
  `@workpulse/api-client` gains `useGetRealtimeBoard` / wire types (WS routes
  are not in OpenAPI; the WS client is hand-written in T5).
- Tests:
  - `tests/integration/test_realtime_api.py`: snapshot == `GET /attendance/
    board` at the same injected `_now` (criterion 1's "never disagree" test);
    manager scope = self + direct reports rows **and** counts; unlinked
    manager → empty; `employee`/`finance` → 403 `forbidden`;
    `client_factory(None)` → 401 `unauthenticated`; tenant B's snapshot never
    contains tenant A's rows; snapshot correct with a raising bus/publisher
    fake (criterion 6).
  - `tests/integration/test_realtime_ws.py` (starlette `TestClient`): denied
    upgrades (no cookie → denial; employee-role override → denial) deliver
    **zero frames** — both reject before any DB/session use so the shared test
    session's loop is never touched (documented fixture note).
  - `tests/unit/test_realtime_stream.py`: snapshot frame first; in-scope fact
    → exactly one `presence.upsert` with recounted counts; out-of-scope fact
    dropped after refresh; team-change re-inclusion; duplicate fact idempotent;
    injected clock.
  - `tests/contract/test_realtime_frames.py`: every produced frame validates
    against `contracts/realtime/board.v1.json`; no PII-shaped keys (mirror
    `test_attendance_events.py`); `test_openapi_drift.py` stays green.

**Verify:** `make gen-api && make check` — API gates + OpenAPI drift green;
web suite unaffected (its `vi.mock` of the client absorbs the new exports).

### T5 — Web: `features/realtime` + `/command-center` · done 2026-10-01

Load `ui-ux-pro-max` **and** `frontend-design` before building (AGENTS rule 15);
load `shadcn` if anything touches `components/ui/`. Follow the presentation
rules (data-panel chrome, `containerLabel` + swipe hint, control language,
`11`-unit touch targets, `font-mono` data labels).

**Changes**

- `apps/web/features/realtime/`:
  `types.ts` (wire re-exports from `@workpulse/api-client`) · `api.ts`
  (`realtimeBoardQueryKey` wrapping `getGetRealtimeBoardQueryKey()`) ·
  `hooks/use-realtime-board.ts` (REST snapshot query with
  `refetchInterval: 60_000` — decision 6's slow refresh — and `ApiError` →
  message mapping) · `hooks/use-presence-socket.ts` (hand-written WS client:
  URL derived from `NEXT_PUBLIC_API_URL` http→ws; auto-reconnect with backoff;
  `snapshot`/`presence.upsert` frames applied via
  `queryClient.setQueryData(realtimeBoardQueryKey, …)` so **server data stays
  in the Query cache** per conventions — no shadow copies; connection status
  is UI state; socket closed on unmount) · `labels.ts` (status → label maps;
  wire values never rendered).
- `apps/web/features/realtime/components/`: `CommandCenter.tsx` (`"use client"`
  leaf) · `PresenceTable` (rows: status `Badge` (success/warning/destructive/
  muted), `late` + `minutes_late`, `since` via `lib/datetime.ts`; data-panel
  chrome; keyboard-scrollable with `containerLabel` + hint) · `LiveCounts`
  (`BoardCountCards`-style `CountCell` tokens: present `text-success`, remote
  `text-info`, late `text-warning`, absent `text-destructive`; overlapping-
  counts copy) · `LiveActivityRail` (`ActivityFeed` patterns) ·
  `LiveStatusBanner` (`Alert`: connecting / "live updates unavailable").
- `apps/web/app/(dashboard)/command-center/page.tsx` — RSC shell
  (`PageHeader` + `CommandCenter`).
- `apps/web/components/destinations.ts` — nav entry `/command-center`,
  `roles: ["manager", "hr", "admin", "super_admin"]` (both navs filter via
  the shared catalog).
- `apps/web/proxy.ts` — add `/command-center` to `DASHBOARD_ROOTS` + matcher.
- Denied state mirrors `UsersTable`'s pattern (non-allowed role →
  `router.replace("/dashboard")` + short notice).
- `apps/web/tests/realtime/` (helpers mirroring `tests/attendance/helpers.tsx`;
  scripted fake via an injectable WebSocket factory / `global.WebSocket` stub):
  snapshot renders rows + counts + names joined from mocked employee data;
  an upsert frame updates cache and counts; WS failure → banner + REST data
  still rendered + refresh scheduled; denied role → redirect; loading/empty
  states.

**Verify:** `make check-web` (tsc + ESLint + Vitest); then `make check` green.

### T6 — E2E: live flows + manager seed · done 2026-10-01

**Changes**

- `database/postgres/seeds/` (extend `003_users.sql` or add `004`): a synthetic
  manager employee (e.g. EMP-DEV02) + `manager@workpulse.test` (role `manager`,
  linked; same dev password/hash) and an idempotent
  `UPDATE employees SET manager_id = … WHERE id = …0021` so Wendy has a
  manager — enabling a true manager-scoped wall (synthetic only; safe re-run).
- `tests/e2e/command-center.spec.ts` (`test.describe.serial`; two-user pattern
  via `browser.newContext()` + `uiLogin` per context — new to the suite;
  `closeOpenRecords`-style cleanup so the worker starts checked out):
  1. **Live** — manager watches `/command-center`, worker checks in on
     `/attendance`: the row flips to present (late badge per shift rules)
     without reload, measured **< 2s** after the check-in response (criterion
     12).
  2. **Reconnect** — close the page's socket from `page.evaluate`; the
     "live updates unavailable" banner shows; a check-in made while
     disconnected is visible after auto-reconnect + snapshot resync
     (criterion 13).
  3. **Denied** — `worker@`: no Command Center nav entry; direct goto renders
     the denied state (criterion 11).
  4. **Cookie-less WS** — `page.evaluate` opens the WS URL in a logged-out
     context: connection rejected, no snapshot frame (criterion 4).

**Verify:** `make check-e2e`, then full `make check` green (e2e is part of it
since 2026-09-29).

### T7 — Record: ADR + docs + status · done 2026-10-01

**Changes**

- `.ai/decisions/0011-realtime-fanout-and-presence-projection.md` (Accepted):
  Phase-2 fan-out = transient per-tenant Redis pub/sub with a per-connection
  presence projection (no Redis rows cache; `minutes_late` recomputed from
  `record.shift_id`; WS denial mechanics; uvicorn ping) and the consequence
  that Phase 3's outbox + Kafka replaces the **input** to the same projection
  and protocol without a protocol change. (Architecture rule: a technology is
  adopted with a spec **and** an ADR.)
- This spec: tick the acceptance criteria, `Status: done`.
- `attendance.md` + `.ai/domain.md`: one-line consumer/wording notes for the
  presence port and the LATE-flag fix.
- `tasks/TODO.md`: Now → Done entry with a one-line summary;
  `.ai/features/README.md` index → done; `AGENTS.md` quickstart table gains
  `make redis-up` if the T2 targets warrant it.

**Verify:** `make check` green; the chain spec → domain → API → protocol → UI
→ test is walkable without searching the repo.

### UI refinement — stacked presence rows on phones · done 2026-10-01

- On phones (< `sm`) `PresenceTable` renders the same rows as stacked entries
  (name + `since`, then status/late badges and the employee code) so every
  presence field is visible without sideways scrolling — the wall is a glance
  surface, not a record browser. The `sm+` table, its `containerLabel`, the
  card chrome and all test ids are unchanged; the "Swipe sideways" hint left
  this card (phones no longer have an overflow region — other list tables keep
  it, per `Presentation rules`). Badges render through shared `StatusBadge` /
  `LateBadge` helpers. Web tests pin both row views inside the card
  (`presence-table-desktop` / `presence-table-mobile`).

**Verify:** `pnpm run typecheck && pnpm run lint && pnpm run test` green (155
web tests); e2e selectors untouched (`presence-${status}` rows still live on
the sm+ table; Playwright runs at desktop viewport).

### UI refinement — bounded live panes · done 2026-10-02

- A whole-tenant board carries one row per active employee, so with the
  presence list uncapped the page grew a screen per hundred absent rows: the
  table header scrolled away, the activity rail sat above a long empty column
  (and, on phones, below the entire list). `PresenceTable` now bounds both row
  views (`max-h-[min(60dvh,36rem)] overflow-y-auto`); its `sm+` table sticks the
  header (`TableHeader sticky`), and the phone stack scrolls in its own
  labelled region. `LiveActivityRail` bounds its feed the same way (the
  guide-rail line lives on the list, so it spans the full scrollable height).
  Presentation only — no contract, data, ordering, copy or test-id changes;
  `components/ui/table.tsx` gains the opt-in `containerClassName` (extra
  scroll-container classes) and `TableHeader sticky` props.
- **Verify:** `pnpm run typecheck && pnpm run lint && pnpm run test` green;
  screenshots reviewed at 375 / 1440 in both themes; e2e selectors unchanged.

### Test strategy

| Layer | What is added | Gate | Failure case covered |
| --- | --- | --- | --- |
| Unit (`tests/unit`) | row projection (`minutes_late`, since), fact building, best-effort publish, `RealtimeService.stream` frame logic | `make check-api` | publisher raising does not break check-in; out-of-scope facts dropped; duplicate facts idempotent |
| Integration (`tests/integration`, postgres-test + redis-test) | bus round-trip + channel isolation; `board_detail` invariants; REST snapshot roles/scope/tenancy; WS denials | `make check-api` | 401/403/denied upgrade (zero frames); cross-tenant invisibility (404/empty); dead-Redis publish and Redis-free snapshot |
| Contract (`tests/contract`) | frames vs `contracts/realtime/board.v1.json`; no-PII keys; OpenAPI drift | `make check-api` | malformed/extra-key frames rejected by schema |
| Web (`apps/web/tests/realtime`) | render states, cache updates from frames, banner/refresh, denied redirect | `make check-web` | WS unreachable (REST fallback visible); denied role |
| E2E (`tests/e2e/command-center.spec.ts`) | two-user live flip <2s; reconnect resync; denied nav; cookie-less WS | `make check-e2e` (in `make check`) | dropped socket mid-session; unauthenticated socket |

No Postgres migration, no `contracts/events/` changes, no audit assertions
(read-only feature). All time-dependent tests use injected clocks
(`monkeypatch _now` / service `now` args), per `.ai/testing.md` rule 5.

### Risks · unknowns · ADR candidates

1. **WS denial status (resolved in T1, 2026-10-01):** verified against the
   installed Starlette 1.7.0 + uvicorn (both WS implementations,
   `websockets` and `websockets-sansio`): the close-before-accept path answers
   a single **403** for both unauthenticated and forbidden — the upgrade
   wording is narrowed to "denied on the upgrade; the 401/403 distinction
   lives on the REST route". Distinct statuses are expressible through the
   ASGI `websocket.http.response` extension (advertised in the scope;
   `websocket.http.response.start` before accept) but are deliberately not
   used: browser JS cannot observe the upgrade status either way.
2. **PresenceState materialization (ADR candidate → ADR 0011):** this plan
   chooses the live per-connection projection over a Redis rows cache (fewer
   staleness paths; snapshot = one PG computation, which ADR 0009 already
   accepted). If snapshot latency under many concurrent viewers ever matters,
   a read-through Redis cache with TTL is the known upgrade — one port swap.
3. **`minutes_late` recompute drift:** recomputed from `record.shift_id` + the
   shift's current `start_time`/`grace_minutes`; editing a shift after a late
   check-in changes the displayed minutes (the `late` flag itself never
   drifts). Accepted; a stored column is the fix if it ever matters.
4. **Counts lag clock-driven transitions:** pushed counts recount the
   connection's known rows, so `absent` onset lands at the next ~60s snapshot
   refresh (decision 6). Visible as counts changing without a row flip — UI
   copy should say the wall refreshes itself.
5. **E2E timing assertion (<2s) flake risk:** measured after the check-in
   response on a serial local run; if a slow machine flakes, degrade the
   assertion to "visible without reload" + a logged delta rather than a hard
   budget (say so when changing it).
6. **Per-connection Redis subscriptions** (N viewers = N subscribers on one
   channel): fine at this scale; a refcounted in-process hub is the known
   upgrade path and would not change the protocol.
7. **Seed addition (`manager@`)** touches shared dev seeds — re-run must stay
   idempotent and `make check-e2e` must stay green for employees/attendance/
   users suites (checked in T6).
8. **TestClient + shared test session loop mismatch** — WS denial tests are
   deliberately pre-DB; if a full in-process WS happy-path test is ever
   wanted, add a per-loop session override (noted in T4).
9. **Two-user e2e pattern is new** to the suite (one user per
   `browser.newContext`, real UI login each); kept serial (`workers: 1`).
10. Unlinked managers see nobody (existing `_scoped_employees` guard) —
    realtime mirrors it exactly; covered by a test, no behavior change.
