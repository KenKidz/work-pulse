# Feature: Realtime Command Center

- **Status:** draft
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

- **PostgreSQL is the source of truth; Redis PresenceState is derived hot
  state.** It must be rebuildable at any time from attendance records +
  employees + shifts; a Redis flush degrades latency, never invents facts.
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
- Infra: a `redis` service in `docker-compose.yml`, `REDIS_URL` in
  `.env.example`, a Redis client wrapper in `app/infrastructure/redis/`.
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
| Infra config | `docker-compose.yml` (redis service) · `.env.example` (`REDIS_URL`) |

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
- **Handshake auth**: `wp_session` cookie resolved via `app/dependencies.py`
  (ADR 0010). Missing/invalid session → upgrade rejected with **401**;
  valid session, insufficient role → **403**. In both cases zero application
  frames are sent. The REST snapshot uses the same resolution and the uniform
  error shape (`unauthenticated`, `forbidden`).
- **Delta contract**: `presence.upsert` is a **full-row idempotent upsert** with
  fresh `counts` — redelivery is harmless and clients never patch partial
  state. At most one delta per attendance transition (a late check-in carries
  `late: true` and `minutes_late` in the row; there is no separate late frame).
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
- **Heartbeat**: the server holds connections with protocol-level ping;
  dead connections are reaped. No application-level `ping`/`pong` frames.
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

- [ ] API: given an `hr` session, when `GET /api/v1/realtime/board` is called,
  then `200` returns `date` = current workday with overlapping counts and
  presence rows that exactly match `GET /attendance/board` semantics (ADR 0009)
  for the same instant.
- [ ] API (live): given a manager's open WS on `/api/v1/realtime/board`, when a
  report checks in, then the socket receives one `presence.upsert` with the
  full row, fresh `counts` and `reason: "checked_in"` — visible on the wall
  within 2 seconds of the check-in response (the Phase-2 metric).
- [ ] API (failure): given an `employee` or `finance` session, when they request
  the snapshot or attempt the upgrade, then `403` (uniform `forbidden`) /
  rejected upgrade, and no board frame is ever delivered.
- [ ] API (failure): given a missing or invalid `wp_session` cookie, then `401`
  (`unauthenticated`) and the upgrade is rejected before any frame.
- [ ] API (tenant isolation): given sockets in two tenants, when tenant A
  checks in, then tenant B receives no frame, and each snapshot contains only
  its own tenant's rows (404/empty rules).
- [ ] API (failure): given Redis is unreachable, when the snapshot is requested,
  then it is still correct from PostgreSQL and no presence state is invented.
- [ ] Contracts: every produced frame validates against
  `contracts/realtime/board.v1.json`, and a test asserts no name/email/note or
  other PII field exists in any frame or snapshot.
- [ ] Unit: the projection (workday, late, minutes_late, absent onset) uses an
  injected clock — no wall-time assertions.
- [ ] Web: given a manager opens `/command-center`, then live counts, the
  who-is-working rows (names joined from employee data) and the activity rail
  render from the snapshot, with loading and empty states.
- [ ] Web (failure): given the WS cannot connect, then the page renders the REST
  snapshot with a visible "live updates unavailable" state and refreshes the
  snapshot about every 60s.
- [ ] Web (denied): given an `employee`/`finance` login, then no command-center
  nav is shown and direct navigation renders the denied state.
- [ ] E2E: an employee checks in while a manager watches `/command-center`, then
  the row flips to `present` (with `late` per the shift) without a reload,
  within 2 seconds.
- [ ] E2E (edge): after the socket drops and reconnects, the wall resyncs from a
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

_Not planned yet._
