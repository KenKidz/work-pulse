# Feature: Transactional outbox + relay

- **Status:** done
- **Phase:** 3
- **Size:** L
- **Surfaces:** api+web+events+workers (web surface untouched — no UI; the
  ladder's only value covering `workers`. Real work: outbox writes + contracts
  in flight + relay)
- **Links:** `.ai/architecture.md` ("Outbox, not dual writes" · Phase roadmap
  row 3) · `.ai/domain.md` (event catalog — the 7 Phase-1 contracts move to
  "Published: 3") · `contracts/events/common/event-envelope.v1.json` ·
  ADR 0006 (`aiokafka` phase gate) · ADR 0009 (late semantics feeding
  `attendance.late`) · ADR 0011 (realtime consumer swap is the follow-up) ·
  producers: `employees.md`, `attendance.md` · future consumers:
  `realtime-command-center.md`, Leave, Notifications

## Problem

WorkPulse's core promise is "one fact, one event" — but the seven versioned
events that exist since Phase 1 are built, contract-tested and then dropped on
the floor: nothing persists or publishes them. Any consumer (the live wall,
notifications, analytics, the Phase-3 approval workflows) would have to poll
OLTP tables or trust a best-effort in-process hop. The obvious shortcut — write
the row, then publish — loses events exactly when it matters (a crash between
the two writes), and the product metric for this phase demands the opposite:
"100% of domain state transitions publish a versioned event; consumers are
replay-safe (idempotent on `event_id`)". The architecture already names the
fix: the row and its outbox event commit in **one PostgreSQL transaction**, and
a **relay** moves committed events to Kafka at-least-once.

## Business rules

1. **Outbox, not dual writes.** The state change, its audit entry and its
   outbox event row commit in one PostgreSQL transaction
   (`.ai/architecture.md`). A rolled-back mutation leaves no event row — ever.
2. **Services append, routers stay thin.** Domains hand their finished envelopes
   to an `EventOutbox` port in `app/shared/` (the `AuditWriter` pattern); the
   Postgres writer runs inside the request's session. No domain imports the
   outbox's SQLAlchemy code or `aiokafka` (architecture rule 3).
3. **Envelopes are stored and published verbatim.** The row holds exactly what
   the domain's `events.py` builder produced; the relay publishes that envelope
   as-is. `event_id` and `occurred_at` are never re-stamped (builders already
   carry the injected clock).
4. **At-least-once delivery.** The relay marks `published_at` only after the
   broker acks; a crash in between re-publishes the same `event_id`. Consumers
   must deduplicate on `event_id` and ignore unknown fields — already required
   by `.ai/conventions.md` → Events. No exactly-once claims.
5. **One topic, per-employee order.** Everything goes to `workpulse.events`,
   message key = the envelope's `data.employee_id`; the relay publishes in
   outbox insertion order, so per-partition (per-employee) order matches commit
   order. v1 runs exactly one relay instance.
6. **Retries, never drops.** A publish failure never blocks or fails a user
   request and never discards a row: the relay retries with backoff, counting
   `attempts` and recording `last_error` (infrastructure error text only — never
   payload contents). Poison parking / DLQ is out of scope.
7. **Published rows are kept** as the append-only record of what went on the
   bus (mirroring `audit_entries`). Pruning/retention is out of scope.
8. **`tenant_id` everywhere.** Every outbox row and envelope carries the writing
   identity's `tenant_id`; the topic is shared across tenants and consumers
   scope by the envelope field.
9. **No PII in payloads** — identifiers and state only (`.ai/security.md`),
   unchanged from the Phase-1 builders.
10. **Time is a parameter.** `occurred_at` is the domain `now` handed to the
    service; `created_at` / `published_at` come from the writer's and relay's
    injected clocks (`.ai/conventions.md`).

## Scope

- `event_outbox` table + Alembic migration `0005` (stored envelope + relay
  bookkeeping columns — shape pinned under Events).
- `EventOutbox` port in `app/shared/` + Postgres writer in
  `apps/api/app/infrastructure/postgres/`, wired in `app/dependencies.py` like
  `PostgresAuditWriter`.
- Wire the 7 existing producers: `employees` (created/updated/transferred/
  terminated) and `attendance` (checked_in/checked_out/late) services append
  their built envelopes in-transaction instead of dropping them.
- Relay worker `workers/relay/`: poll unpublished rows in insertion order →
  publish via `aiokafka` → mark `published_at`; retry with backoff on failure;
  graceful shutdown mid-batch.
- Kafka single-broker (KRaft) in `docker-compose.yml`: `kafka` (dev) +
  `kafka-test` (throwaway, tmpfs) mirroring `postgres-test` / `redis-test`;
  `.env.example` updated.
- `aiokafka` dependency — ADR 0006's pre-agreed Phase-3 gate; this spec is its
  justification (rule 10).
- Makefile: `kafka-up` / `kafka-test-up` / `dev-relay`; `check-api` brings up
  `kafka-test`.
- Tests per surface (Acceptance criteria), and every existing gate stays green.
- ADR 0012 recording the transport pins (topic + keying, at-least-once, outbox
  row shape).

## Out of scope

- **The realtime consumer swap.** The Command Center keeps its Phase-2
  best-effort Redis publish; per ADR 0011 the Kafka-fed consumer that replaces
  it is its own follow-up, where the ≤2s wall metric is re-proven through the
  relay hop.
- **Consumers of any kind** — workflow, notification and analytics workers
  belong to the Leave, Notifications and Analytics specs.
- New event types: `leave.*`, `overtime.*`, `attendance.correction_requested`
  arrive with their features; organizations and identity emit nothing (not in
  the `.ai/domain.md` catalog).
- Exactly-once transactions, Kafka Streams / ksqlDB, schema registry, compacted
  topics, dead-letter / poison parking.
- Horizontal relay scaling or multi-instance claiming (v1 is one instance).
- Outbox pruning/retention, an admin/ops HTTP surface for the outbox, metrics
  dashboards (Phase 6).
- Web/UI of any kind.

## Surfaces & files

| Surface | Location |
| --- | --- |
| Outbox port | `apps/api/app/shared/outbox.py` (called from `app/domains/employees/service.py` + `app/domains/attendance/service.py`) |
| Persistence | `apps/api/app/infrastructure/postgres/models_outbox.py` · `apps/api/app/infrastructure/postgres/outbox.py` · migration `apps/api/alembic/versions/0005_*` |
| API router | — (no routes added or changed) |
| Events | `contracts/events/` — existing 7 payloads + `common/event-envelope.v1.json`; transport pins under Events below |
| Workers | `workers/relay/` (poll loop + `aiokafka` publisher; shares `apps/api` settings + outbox repository via the monorepo — exact wiring is the plan's call) |
| Web feature / routes | — (none) |
| API tests | `apps/api/tests/unit` (envelope/port validation, retry bookkeeping) · `tests/integration` (atomicity, all 7 producers, relay ↔ `kafka-test`) · `tests/contract` (persisted payloads vs `contracts/`) |
| E2E | `tests/e2e/` — regression only (must pass with the relay stopped) |
| Infra | `docker-compose.yml` (`kafka`, `kafka-test`) · `Makefile` · `.env.example` |

## Roles & access

- No new routes or UI — no role matrix rows change. The outbox is written only
  as part of already-authorized mutations; a denied mutation writes nothing.
- The relay is a trusted system process: no user identity, it reads every
  tenant's rows (the documented cross-tenant reader alongside the audit
  writer), and every published envelope carries its `tenant_id` for consumers
  to scope by.
- Existing denied-role tests for check-in/check-out and employee mutations stay
  the guard for anything mutating.

## API contract

None added or changed. No route, request or response shape moves;
`make gen-api` produces no diff and the OpenAPI-drift contract test stays
green. The transport contract this feature adds is pinned under Events.

## Events

No new contracts — the 7 Phase-1 contracts are exactly what now flows:

| `event_type` | Producer (builders already exist) |
| --- | --- |
| `employee.created` / `updated` / `transferred` / `terminated` | `apps/api/app/domains/employees/events.py` |
| `attendance.checked_in` / `checked_out` / `late` | `apps/api/app/domains/attendance/events.py` |

**Transport contract (pinned — recorded as ADR 0012 at build time).**

Outbox row `event_outbox` (migration `0005`) — sketch, DDL detail is the
plan's:

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `uuid` PK (UUIDv7) | row id; time-ordered ≈ insertion order for relay polling |
| `event_id` | `text` UNIQUE | the envelope's `evt_…` — idempotency key |
| `tenant_id` | `uuid` NOT NULL | envelope's tenant (indexed) |
| `event_type` | `text` NOT NULL | e.g. `attendance.checked_in` (indexed) |
| `payload` | `jsonb` NOT NULL | the envelope exactly as built |
| `occurred_at` | `timestamptz` NOT NULL | envelope's domain time |
| `created_at` | `timestamptz` NOT NULL | write time (writer's clock) |
| `published_at` | `timestamptz` NULL | set by the relay after broker ack |
| `attempts` | `int` NOT NULL DEFAULT 0 | publish attempts |
| `last_error` | `text` NULL | last publish failure — infrastructure error text only, never payload contents |

Plus a partial index over unpublished rows for the relay's poll.

Kafka message:

| Aspect | Pin |
| --- | --- |
| Topic | `workpulse.events` (single topic; contract versioning lives in the envelope's `version`, not the topic name) |
| Key | UTF-8 `data.employee_id` — per-employee (per-partition) ordering |
| Value | the stored envelope as UTF-8 JSON — same `event_id`, same `occurred_at`, semantically identical, never rebuilt |
| Headers | none in v1 |
| Delivery | at-least-once; consumers MUST dedupe on `event_id` and ignore unknown fields |

## Edge cases

- Crash after broker ack but before `published_at` is set → the row publishes
  again with the identical `event_id` (rule 4); the duplicate is harmless to
  consumers.
- Late check-in emits `attendance.checked_in` + `attendance.late` in one
  transaction → two rows, published in builder order under the same key.
- Broker down for hours → backlog accumulates in PostgreSQL; user requests are
  unaffected; the relay drains in order once the broker returns.
- A builder ever emitting a non-envelope payload → the port rejects it and the
  transaction fails loudly — never a silently stored junk row.
- Relay restart mid-batch → marked rows are skipped, unmarked rows re-send
  (at-least-once, same `event_id`).
- `last_error` must never echo payload contents (PII rule); only infrastructure
  error text.
- Outbox growth is accepted (rows kept by decision 2); retention, if ever
  needed, is a separate ops decision.

## Acceptance criteria

- [x] API (integration): given an authenticated check-in, when the request
      commits, then one `event_outbox` row holds the `attendance.checked_in`
      envelope and — when the check-in is late — a second row holds
      `attendance.late`, both committed in the same transaction as the
      attendance record and its audit entry.
- [x] API (integration — all producers): given each of the 7 catalogued
      transitions (`employee.created`/`updated`/`transferred`/`terminated`,
      `attendance.checked_in`/`checked_out`/`late`), when it commits, then
      exactly one outbox row per emitted envelope exists, stamped with the
      actor's `tenant_id`.
- [x] API (failure — atomicity): given a service call forced to fail after its
      outbox append (stubbed audit writer raising), when the transaction rolls
      back, then neither the business row nor any outbox row survives.
- [x] API (failure — denied): given a denied-role mutation (`403`), when the
      request is rejected, then no outbox row is written; the existing
      denied-role tests stay green.
- [x] API (tenant isolation): given outbox rows written under two tenants, then
      no row's `tenant_id` ever differs from its envelope's or the writing
      identity's, and every repository read is tenant-scoped (this feature adds
      no cross-tenant read surface).
- [x] Events (contract): given every persisted envelope, when validated, then
      it matches `event-envelope.v1.json` and its type's `v1` payload schema;
      the port rejects a non-envelope payload at write time (failure case).
- [x] Workers: given committed unpublished rows, when the relay runs against
      `kafka-test`, then each envelope arrives on `workpulse.events` with
      `key = data.employee_id` and a `value` that parses to the stored envelope
      unchanged, and each row gets `published_at`.
- [x] Workers (ordering): given a late check-in emitting `checked_in` + `late`
      in one transaction, when the relay publishes, then `checked_in` reaches
      the partition before `late`.
- [x] Workers (failure): given an unreachable broker, when the relay runs, then
      no row is marked published, `attempts` increments, `last_error` records
      the failure without echoing the payload, and API writes keep committing;
      once the broker returns, a later run publishes the same rows with
      unchanged `event_id`s.
- [x] Workers (crash window): given a publish that succeeded but whose row was
      not marked (crash between broker ack and `published_at`), when the relay
      re-runs, then the row is published again with the identical `event_id` —
      at-least-once proven.
- [x] Web: no web surface — `make check-web` stays green with zero web diffs.
- [x] Gate: `make check` green with the relay stopped — every existing e2e flow
      (check-in/out, employees, command center) passes without Kafka running,
      proving the write path never depends on the bus.

## Decisions (PO, 2026-10-02)

1. **Backbone only.** This spec is the outbox + relay publishing the 7 existing
   events; the Kafka-fed realtime consumer (ADR 0011's promise) is a separate
   follow-up so the ≤2s wall metric is re-proven through the relay hop on its
   own. Rationale: smallest reversible slice; the wall keeps working meanwhile.
2. **Published rows are kept** — append-only history of what went on the bus,
   mirroring `audit_entries`; pruning/retention deferred until size or Phase-6
   observability demands it. Rationale: replayability is the phase's point; a
   deleted row cannot be inspected when a consumer misbehaves.
3. **One topic `workpulse.events`, message key = `data.employee_id`** (all 7
   events carry one), value = the envelope JSON as stored; consumers filter on
   `event_type`. Rationale: per-employee broker ordering matches the domain's
   consistency needs (one employee's transitions in order) without per-domain
   topics to provision and version; recorded as **ADR 0012**.
4. **Real broker in the gate** — `kafka` + `kafka-test` compose services
   mirroring `postgres-test`/`redis-test`; an integration test drives the relay
   and consumes the bytes back from the topic; `make check-api` brings
   `kafka-test` up. Rationale: the wire format and at-least-once behavior are
   the feature; a fake publisher would prove only our own loop.

## Plan

> Produced by `/plan`, 2026-10-02. No API routes and no web surface, so the
> "contract first" step pins the **transport** contract (outbox row + Kafka
> message → ADR 0012) before any producer or consumer code (workflow rule 4);
> the 7 event contracts under `contracts/events/` already exist and do not
> move. Build order: contract → infra → outbox core → producers (attendance,
> employees) → persisted-envelope contract proof → relay → relay proofs →
> record. `make check` stays green after every task.

### Acceptance criteria → changes (restated)

| # | Criterion (restated) | Satisfied by |
| --- | --- | --- |
| AC1 | Check-in commits one `attendance.checked_in` outbox row (+ `attendance.late` row when late) in the same PostgreSQL transaction as the attendance record and its audit entry | T4 — `AttendanceService` appends its `produced` envelopes before the audit write (same request transaction via `get_session`); proven in `tests/integration/test_outbox_attendance.py` |
| AC2 | Each of the 7 catalogued transitions (`employee.created`/`updated`/`transferred`/`terminated`, `attendance.checked_in`/`checked_out`/`late`) leaves exactly one outbox row per emitted envelope, stamped with the actor's `tenant_id` | T4 (3 attendance) + T5 (4 employee — these builders get **new** call sites; see finding F1) + sweep test in `tests/integration/test_outbox_producers.py` |
| AC3 | A service call that fails after its outbox append (stubbed audit writer raising) rolls back: neither the business row nor any outbox row survives | T3 (writer-level rollback test) + T4 (service-level test monkeypatching `PostgresAuditWriter.write` to raise) |
| AC4 | A denied-role mutation (403) writes no outbox row; existing denied-role tests stay green | T4/T5 — denied cases assert `event_outbox` count = 0; existing `test_*_roles.py` untouched and green |
| AC5 | With rows from two tenants: no row's `tenant_id` differs from its envelope's or the writer identity's; every repository read is tenant-scoped (no cross-tenant read surface added) | T5 — two-tenant stamping test; the only outbox reader is the relay repository (documented cross-tenant system reader); no route is added (`make gen-api` diff-free) |
| AC6 | Every persisted envelope validates against `event-envelope.v1.json` + its type's `v1` payload schema; the port rejects a non-envelope payload at write time (failure case) | T3 (port rejects — unit failure case) + T6 (persisted rows vs `contracts/` with `jsonschema.Draft202012Validator`) |
| AC7 | Committed unpublished rows reach `workpulse.events` via the relay with `key = data.employee_id`, a value that parses to the stored envelope unchanged, and each row gains `published_at` | T8 — real `OutboxRelay` + `KafkaEnvelopePublisher` against `kafka-test`, consumed back with `AIOKafkaConsumer` |
| AC8 | A late check-in (`checked_in` + `late`, one transaction) reaches the partition in that order | T8 — offset ordering on the same partition; guaranteed by relay poll `ORDER BY id` (UUIDv7 monotonic, see risk R9) |
| AC9 | Unreachable broker: no row marked published, `attempts` increments, `last_error` records infra text without echoing payload, API writes keep committing; after recovery the same rows publish with unchanged `event_id`s | T7 (bookkeeping unit tests incl. no-payload-echo) + T8 (dead-bootstrap run, concurrent API write, then recovery run) |
| AC10 | Crash between broker ack and `published_at` → re-run republishes the identical `event_id` (at-least-once) | T8 — `mark_published` fault-injected once after a real send, re-run consumes the same `event_id` twice |
| AC11 | No web surface — `make check-web` green with zero web diffs | T9 — no `apps/web/` file is touched at any point |
| AC12 | `make check` green with the relay stopped — e2e flows pass without Kafka, proving the write path never depends on the bus (narrowed by the realtime consumer, D2: the wall's live-delta e2e now runs through the hop; the write path and snapshot/resync flows remain bus-free) | T9 — `check-e2e` deliberately gains no Kafka dependency (Playwright starts only uvicorn + next dev); `check-api` uses `kafka-test` for relay proofs only |

### Repo research findings the plan relies on

Read-only research (subagent) + verification. Confirmed matches: the
`AuditWriter` port pattern (`app/shared/audit.py` → `PostgresAuditWriter`
(session-scoped add+flush) → `PostgresAuditWriter(session)` construction in
`app/dependencies.py`); commit is **per-request** in `get_session`
(`app/infrastructure/postgres/session.py`) — an outbox `add`+`flush` is
automatically atomic with the business row and audit entry; builders return
full envelope **dicts** with `evt_<uuid7>` ids and injected clock; IDs are
UUIDv7 (`uuid-utils`); `jsonschema` is already a dev dependency and contract
tests use `Draft202012Validator`; e2e never touches Kafka.

Deviations from the spec's assumptions (handled in the plan):

- **F1 — employee builders are not "currently dropped".** Only attendance
  builds envelopes in its service and drops them at the router
  (`app/api/v1/attendance.py` `.record`). The 4 `employees/events.py` builders
  have **zero production call sites** — T5 adds them. `EmployeeService.update`
  with an empty diff early-returns (no transition → no event); `terminate`'s
  `manager_cleared` audit entries emit nothing (not in the event catalog).
- **F2 — settings live in `app/config.py`** (not `settings.py`); new knobs
  follow the existing lower-field/UPPERCASE-env pattern.
- **F3 — no `.env.example` exists.** The root `.env` and `apps/api/.env` double
  as templates; T2 updates those instead.
- **F4 — `workers/` does not exist and there is no uv workspace**; `apps/api`
  is `[tool.uv] package=false`. Relay wiring decision (ADR 0012): run the
  relay inside the `apps/api` uv environment via PYTHONPATH (details in T7).

### Ordered tasks

**T1 — Pin the transport contract (ADR 0012) — docs only.**
New `.ai/decisions/0012-outbox-transport-and-relay.md` (existing ADR format:
Status/Date/Context/Decision/Consequences) recording: topic `workpulse.events`
(versioning lives in the envelope's `version`), key = UTF-8 `data.employee_id`,
value = the stored envelope as UTF-8 JSON (semantically identical — PostgreSQL
`jsonb` may normalize object key order; `event_id`/`occurred_at` never
re-stamped), no headers, at-least-once with consumer dedupe on `event_id`, the
`event_outbox` row shape (Events table above), `attempts` = every publish
attempt (success included), published rows kept append-only, topic ensured by
the relay at startup (1 partition / rf 1 in v1, auto-create as backstop), and
the relay wiring + UUIDv7-ordering decisions (risks R6, R9).
*Verify:* review only; `make check-api` trivially green.

**T2 — Kafka infra + settings.**
`docker-compose.yml`: `kafka` (dev) + `kafka-test` mirroring
`postgres-test`/`redis-test` — single-node KRaft `apache/kafka:3.9.x`,
loopback ports `127.0.0.1:9092` / `127.0.0.1:9093`, `kafka-test` with tmpfs
log dirs, healthchecks (`kafka-topics.sh --bootstrap-server localhost:9092
--list`) so `docker compose up -d --wait` works. `Makefile`: `kafka-up`,
`kafka-test-up`; `check-api` gains `kafka-test-up`. `apps/api/app/config.py`:
`kafka_bootstrap_servers`, `kafka_events_topic`, `kafka_events_topic_partitions`,
`relay_batch_size`, `relay_poll_seconds`, `relay_backoff_base_seconds`,
`relay_backoff_max_seconds` (defaults for local dev). Root `.env` +
`apps/api/.env`: `KAFKA_*` entries (F3).
*Verify:* `docker compose up -d --wait kafka-test` healthy on :9093;
`make check-api` green (gate now waits for kafka-test).

**T3 — Outbox core: port, model, migration, writer.**
- `apps/api/app/shared/outbox.py` (new): `EventEnvelope` pydantic model — the
  7 envelope fields of `event-envelope.v1.json` (event_id pattern `evt_…`,
  event_type pattern, `version >= 1`, tenant_id/actor_id, occurred_at, data)
  **plus required `data.employee_id`** (the Kafka key source; all 7 payloads
  carry it) — `InvalidEnvelopeError`, and `EventOutbox(Protocol)` with
  `async def append(self, envelope: dict[str, Any]) -> None` (the
  `AuditWriter` shape).
- `apps/api/app/infrastructure/postgres/models_outbox.py` (new):
  `EventOutboxRow` → `event_outbox`, exactly the Events table above
  (`models_audit.py` style: `Mapped[]`/`mapped_column`, `JSONB` payload,
  `default=uuid7` id), `uq_event_outbox_event_id`,
  `ix_event_outbox_tenant_id`, `ix_event_outbox_event_type`, partial
  `ix_event_outbox_unpublished (id) WHERE published_at IS NULL`.
- `apps/api/alembic/versions/0005_event_outbox.py` (new; `revision "0005"`,
  `down_revision "0004"`); `alembic/env.py` imports `models_outbox`;
  `tests/integration/conftest.py` appends `event_outbox` to `ALL_TABLES`.
- `apps/api/app/infrastructure/postgres/outbox.py` (new):
  `PostgresEventOutbox(session, clock)` —
  validate via `EventEnvelope` (raise `InvalidEnvelopeError` on anything else —
  loud transaction failure), then `add` a row whose `payload` is **the input
  dict verbatim**, `created_at=self._clock()` (injected clock — time is a
  parameter), `flush()`. Never re-stamps `event_id`/`occurred_at`.
  `PostgresOutboxRepository` (relay side): `list_unpublished(limit)` ordered
  by `id`, `mark_published(id, at)` and `mark_failed(id, error)` — both
  increment `attempts`; `mark_failed` writes `last_error`.
  No `dependencies.py` change yet (lands with T4/T5).
*Tests:* `apps/api/tests/unit/outbox/test_port.py` — the 7 builder outputs
pass; missing `event_id`, non-envelope dict, and envelope without
`data.employee_id` are rejected (failure case). `tests/integration/test_outbox_writer.py`
— append two envelopes on the fixture session: rows exist with verbatim
payloads and injected-clock `created_at`; a raise after append leaves nothing
behind (AC3 writer half).
*Verify:* `make check-api` green; `uv run alembic upgrade head && downgrade -1
&& upgrade head` against `db-test` (or the existing migration suite covers it).

**T4 — Attendance producers (3 events).**
`app/domains/attendance/service.py`: `AttendanceService.__init__(…, outbox:
EventOutbox)`; in `check_in`, after `produced` is complete (incl.
`attendance_late`), append each envelope **in builder order, before** the
presence publish and the audit write; in `check_out`, hoist
`attendance_checked_out(…)` into a `produced` list before the audit write and
append it (the returned `AttendanceMutation` stays shape-identical — router
untouched). `app/dependencies.py`: `get_attendance_service` passes
`PostgresEventOutbox(session, clock=_utc_now)` (module-level `_utc_now()`).
`tests/unit/attendance/conftest.py` + new `tests/unit/outbox/fakes.py`
(`RecordingEventOutbox` — records envelopes, optional raise hook) wired into
the `service` fixture.
*Tests:* `tests/integration/test_outbox_attendance.py` — AC1 (late check-in →
exactly 2 rows, `checked_in` then `late` by id, `tenant_id` = actor's, payload
= the built envelopes, record + audit present); AC3 service half (monkeypatch
`PostgresAuditWriter.write` to raise → assert no attendance row and no outbox
row survive — see risk R2); AC4 attendance half (denied mutation → outbox
count 0). Unit: `check_in`/`check_out` append the right envelopes in order.
*Verify:* `make check-api` green — all existing attendance suites must stay
green (`AttendanceMutation` and the router are unchanged).

**T5 — Employee producers (4 events).**
`app/domains/employees/service.py`: `EmployeeService.__init__(…, outbox:
EventOutbox)`; in `create`/`update`/`transfer`/`terminate` build the matching
envelope from `app.domains.employees.events` and append it before the audit
write — args from state already in hand: `create` → ids + employment_type from
the created row; `update` → `changed_fields=changes.keys()` (names only);
`transfer` → `from_department_id=employee.department_id`,
`to_department_id=department_id`, `effective_from`; `terminate` →
`effective_date=payload.effective_date`. Empty-diff `update` keeps its early
return (no transition, no event); `manager_cleared` audit writes emit nothing.
`app/dependencies.py`: `get_employee_service` passes the outbox.
`tests/unit/employees/conftest.py`: `RecordingEventOutbox` fixture.
*Tests:* `tests/integration/test_outbox_producers.py` — AC2 sweep (all 7
transitions parametrized: exactly one row per emitted envelope, correct
`event_type`, `tenant_id` = actor's); AC5 (two tenants → every row's
`tenant_id` matches its envelope's and the writer identity's). Unit: each
method appends exactly its one envelope with the right `data`.
*Verify:* `make check-api` green; `make gen-api && git diff --exit-code` —
OpenAPI + `@workpulse/api-client` unchanged (existing `test_openapi_drift` as
backstop).

**T6 — Contract proof of persisted envelopes.**
`apps/api/tests/integration/test_outbox_contracts.py` (new; lands in
`tests/integration/`, not `tests/contract/`, because it must read **persisted**
rows through the session — the only deviation from the Surfaces table):
drive each of the 7 transitions through the HTTP API, then validate every
stored `payload` with `Draft202012Validator` against
`contracts/events/common/event-envelope.v1.json` + its type's `v1` payload
schema (map `event_type` → schema file, mirroring
`tests/contract/test_employee_events.py` helpers), and rerun the PII-key sweep
on stored payloads (AC6 happy half; the port-rejects failure case is T3).
*Verify:* `make check-api` green.

**T7 — Relay worker.**
New `workers/relay/` (plus `workers/__init__.py`, `workers/relay/__init__.py`):
- `publisher.py` — `KafkaEnvelopePublisher`: `start()` ensures topic
  `workpulse.events` via `AIOKafkaAdminClient` (per ADR 0012); `publish(env)`
  = `send_and_wait(topic, key=env["data"]["employee_id"].encode("utf-8"),
  value=json.dumps(env).encode("utf-8"))`; `stop()`.
- `relay.py` — `OutboxRelay(session_factory, repo, publisher, *, batch_size,
  poll_seconds, backoff_base, backoff_max, clock)`: poll up to `batch_size`
  unpublished rows **ordered by `id`** → per row `publish` then
  `mark_published(id, clock())`; on failure `mark_failed(id, _safe_error(exc))`
  (exception type + truncated message — never payload), **stop the batch**
  (strict order), backoff `min(base * 2**consecutive_failures, max)` between
  polls; graceful shutdown via `stop()`/event checked between rows and before
  each poll (in-flight row finishes first).
- `main.py` — reads `get_settings()`, builds engine/session factory from
  `app.infrastructure.postgres.engine`, SIGINT/SIGTERM → `relay.stop()`,
  `asyncio.run`.
Wiring (ADR 0012): the relay runs **inside the `apps/api` uv environment** —
`aiokafka>=0.12` joins `apps/api/pyproject.toml` dependencies (ADR 0006's
Phase-3 gate, justified by this spec) and `uv.lock`; pytest
`pythonpath = [".", ".."]` in `apps/api/pyproject.toml` so tests can `import
workers.relay…`; `Makefile` `dev-relay: cd $(API_DIR) && PYTHONPATH="..:." uv
run python -m workers.relay.main`.
*Tests:* `apps/api/tests/unit/outbox/test_relay.py` (stub repo/publisher):
happy batch marks in order; failure increments `attempts` and writes
`last_error` **without payload echo** (exception text embeds a sentinel which
must not appear), stops the batch and preserves order; backoff growth + cap;
stop event ends the loop between rows, leaving the next row unmarked
(graceful-shutdown proof).
*Verify:* `make check-api` green (unit half needs no broker); manual smoke:
`make kafka-up db-up dev-api dev-relay`, run a check-in, watch the envelope
arrive on the topic.

**T8 — Relay ↔ `kafka-test` integration proofs.**
`apps/api/tests/integration/test_outbox_relay.py` (new) — real `OutboxRelay` +
`KafkaEnvelopePublisher` against `kafka-test` :9093, read back with a real
`AIOKafkaConsumer`:
- AC7: seed unpublished rows via real check-in/check-out requests → run until
  drained → each message has `key == data.employee_id`, `value` parses to the
  stored envelope unchanged (same `event_id`/`occurred_at`/`data`), rows gain
  `published_at`.
- AC8: late check-in → `checked_in` and `late` hit the **same partition**,
  `checked_in` at the lower offset.
- AC9: publisher pointed at an unreachable bootstrap (`127.0.0.1:1`, tight
  timeouts so failures surface in seconds) → nothing marked, `attempts`
  incremented, `last_error` is infra text only; an API mutation still commits
  meanwhile; re-run against `kafka-test` → the **same** rows publish with
  unchanged `event_id`s. (Fallback if the dead port proves flaky: a stub
  publisher raising `KafkaConnectionError` for these assertions; the happy
  path stays real.)
- AC10: fault-inject `mark_published` to raise once **after** a successful
  send → row unmarked; restore, re-run → the identical `event_id` is consumed
  twice (at-least-once).
*Verify:* `make check-api` green with `kafka-test` up (T2 wired it into the
gate).

**T9 — Record + full-gate proof.**
This spec (tick all ACs; Status → done at implementation end),
`tasks/TODO.md` (tick), `.ai/domain.md` (event catalog: the 7 contracts move
to "Published: 3").
*Verify:* `make check` with the dev relay **stopped** (AC12 — `check-e2e`
starts only uvicorn + next dev, no Kafka) and `make check-web` with zero web
diffs (AC11); `make gen-api` diff-free once more.

### Risks, unknowns, ADR-worthy decisions

1. **R1 — employee builders need new call sites** (finding F1).
   The plan adds them in T5; `update`/`terminate` edge semantics (empty diff,
   `manager_cleared`) pinned there.
2. **R2 — AC3's rollback proof depends on test-session semantics.**
   `tests/integration/conftest.py`'s `_override_get_session` shares the
   fixture session with production-like commit/rollback-on-exception in
   `get_session`. T4's task includes checking that the override rolls back on
   service exceptions (or emulating it with a SAVEPOINT) — otherwise the
   "nothing survives" assertion is vacuous.
3. **R3 — `attempts` semantics were ambiguous** ("publish attempts"). Pinned:
   it counts **every** publish attempt, success included (incremented in the
   same UPDATE as `published_at`/`last_error`). Recorded in ADR 0012.
4. **R4 — "verbatim" vs `jsonb`.** `jsonb` normalizes object key order, so
   the published bytes are `json.dumps(stored_payload)` — semantically
   identical (same `event_id`, `occurred_at`, `data`), keys possibly
   reordered. The AC wording ("parses to the stored envelope unchanged") is
   tested semantically, not byte-wise. ADR 0012.
5. **R5 — runtime port validation is pydantic, not jsonschema.**
   `jsonschema` is a dev dependency; the port's write-time rejection uses the
   `EventEnvelope` model (structure + `data.employee_id`), while full
   Draft-2020-12 conformance of persisted payloads is proven in T6. Also:
   the port **requires `data.employee_id`** (every builder supplies it) — the
   transport pin needs it as the message key. ADR 0012.
6. **R6 — relay wiring: shared `apps/api` venv + PYTHONPATH** (T7). Cheapest
   honest wiring given `package=false` and no uv workspace; `aiokafka`
   therefore lands in `apps/api` deps even though only the relay uses it.
   Alternative considered: uv workspace / separate `workers/relay` project
   with a path dependency — deferred until a second worker arrives. If the
   worker count grows, revisit as its own ADR.
7. **R7 — `.env.example` does not exist** (finding F3): the two `.env`
   template files are updated instead. If the PO wants a literal
   `.env.example`, it is a one-file copy — decide at review.
8. **R8 — topic provisioning:** the relay ensures `workpulse.events` at
   startup (admin create, ignore-exists; 1 partition / rf 1 in v1) with
   broker auto-create as backstop. Partition growth later preserves the
   keying pin. ADR 0012.
9. **R9 — UUIDv7 poll order:** verified `uuid-utils` `uuid7()` is monotonic
   within a millisecond (docstring + 2000-id probe strictly increasing), so
   `ORDER BY id` preserves same-transaction append order (AC8's
   `checked_in`→`late` pair). Caveat: monotonicity is per-process; two API
   processes could interleave same-ms ids across transactions — acceptable
   (the spec already hedges "≈ insertion order"; per-employee writes are
   serialized per employee). ADR 0012.
10. **R10 — strict order + stop-on-first-failure can head-of-line-block** on
    a permanently poisoned row. Accepted by decisions 1–2 and rule 6
    ("retries, never drops"; DLQ out of scope) — but it is the known failure
    mode of this design; surface it in Phase-6 observability.
11. **R11 — `check-api` now needs Docker capacity for kafka-test** (plus the
    existing postgres-test/redis-test). Same class of dependency as today;
    noted for CI sizing (Phase 8).
12. **R12 — ADR 0012** (T1) is the only new ADR; no other decision here is
    expensive to reverse (the realtime consumer swap stays ADR 0011's
    follow-up; row shape + topic + keying are the reversibility-sensitive
    pins).

### Test strategy (gates × failure cases)

| Gate | What runs | New/changed tests | Failure cases covered |
| --- | --- | --- | --- |
| `make check-api` (ruff + pyright + pytest vs `postgres-test`/`redis-test`/**`kafka-test`**) | unit + integration + contract | **new** `tests/unit/outbox/` (`test_port.py`, `test_relay.py`), `tests/integration/test_outbox_writer.py`, `test_outbox_attendance.py`, `test_outbox_producers.py`, `test_outbox_contracts.py`, `test_outbox_relay.py`; **changed** `tests/unit/attendance/conftest.py`, `tests/unit/employees/conftest.py` (+ `RecordingEventOutbox`), `tests/integration/conftest.py` (`ALL_TABLES`) | port rejects non-envelope (unit); rollback leaves no rows (writer + service); denied role writes nothing; tenant stamping never diverges; broker down → bookkeeping without payload echo, rows kept, API unaffected, recovery re-publishes same `event_id`s; crash window → duplicate `event_id` (at-least-once); ordering under late check-in |
| `make check-web` | typecheck + lint + vitest | none — zero web diffs (AC11) | n/a |
| `make check-e2e` / `tests/e2e/` | Playwright vs dev stack **without Kafka or the relay** | none — regression only | proves the write path never depends on the bus (AC12) |
| existing suites | unchanged | `test_openapi_drift` (no API drift), `test_*_events.py` contract suites (builders untouched), `test_*_roles.py` (AC4 guard), `test_migrations` | green without edits |

Per-domain rule (`.ai/testing.md`) is satisfied on every surface this feature
touches: API happy path + failure cases above, workers happy + ordering +
failure + crash-window, events contract over **persisted** payloads, web has
no surface.
