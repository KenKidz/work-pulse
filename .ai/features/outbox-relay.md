# Feature: Transactional outbox + relay

- **Status:** draft
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

- [ ] API (integration): given an authenticated check-in, when the request
      commits, then one `event_outbox` row holds the `attendance.checked_in`
      envelope and — when the check-in is late — a second row holds
      `attendance.late`, both committed in the same transaction as the
      attendance record and its audit entry.
- [ ] API (integration — all producers): given each of the 7 catalogued
      transitions (`employee.created`/`updated`/`transferred`/`terminated`,
      `attendance.checked_in`/`checked_out`/`late`), when it commits, then
      exactly one outbox row per emitted envelope exists, stamped with the
      actor's `tenant_id`.
- [ ] API (failure — atomicity): given a service call forced to fail after its
      outbox append (stubbed audit writer raising), when the transaction rolls
      back, then neither the business row nor any outbox row survives.
- [ ] API (failure — denied): given a denied-role mutation (`403`), when the
      request is rejected, then no outbox row is written; the existing
      denied-role tests stay green.
- [ ] API (tenant isolation): given outbox rows written under two tenants, then
      no row's `tenant_id` ever differs from its envelope's or the writing
      identity's, and every repository read is tenant-scoped (this feature adds
      no cross-tenant read surface).
- [ ] Events (contract): given every persisted envelope, when validated, then
      it matches `event-envelope.v1.json` and its type's `v1` payload schema;
      the port rejects a non-envelope payload at write time (failure case).
- [ ] Workers: given committed unpublished rows, when the relay runs against
      `kafka-test`, then each envelope arrives on `workpulse.events` with
      `key = data.employee_id` and a `value` that parses to the stored envelope
      unchanged, and each row gets `published_at`.
- [ ] Workers (ordering): given a late check-in emitting `checked_in` + `late`
      in one transaction, when the relay publishes, then `checked_in` reaches
      the partition before `late`.
- [ ] Workers (failure): given an unreachable broker, when the relay runs, then
      no row is marked published, `attempts` increments, `last_error` records
      the failure without echoing the payload, and API writes keep committing;
      once the broker returns, a later run publishes the same rows with
      unchanged `event_id`s.
- [ ] Workers (crash window): given a publish that succeeded but whose row was
      not marked (crash between broker ack and `published_at`), when the relay
      re-runs, then the row is published again with the identical `event_id` —
      at-least-once proven.
- [ ] Web: no web surface — `make check-web` stays green with zero web diffs.
- [ ] Gate: `make check` green with the relay stopped — every existing e2e flow
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

<!-- Filled in by /plan. Ordered tasks naming files + verification. -->

_Not planned yet._
