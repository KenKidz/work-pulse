# Feature: Realtime consumer (the ADR 0011 input swap)

- **Status:** draft
- **Phase:** 3
- **Size:** M
- **Surfaces:** api+events+workers (web surface untouched — no UI; the wall's
  frames are contract-pinned and unchanged)
- **Links:** ADR 0011 (the promise this closes), ADR 0012 (outbox transport),
  `.ai/features/outbox-relay.md` (the backbone consumed),
  `.ai/features/realtime-command-center.md` (the wall fed),
  `.ai/domain.md` event catalog, `.ai/product.md` goal 2

## Problem

The Command Center's live deltas still bypass the event backbone:
`AttendanceService` publishes a `PresenceFact` to Redis directly after each
capture — a dual write beside the transactional outbox row — so the wall has
never actually been fed by the domain events it is supposed to be a projection
of. ADR 0011 promised the swap ("the realtime consumer replaces the in-process
presence-delta port as the **input** to this same projection and protocol —
`attendance.*` events feed the identical frames with no protocol change"), and
the outbox-relay spec deliberately deferred it ("Backbone only … so the ≤2s wall
metric is re-proven through the relay hop on its own"). `.ai/product.md` goal 2
("one fact, one event … realtime state … [is] a projection of it") is not met
until the wall's only input is `workpulse.events`.

## Business rules

- **The bus is the wall's only input.** `AttendanceService` stops publishing
  presence facts; the realtime consumer becomes the sole `PresenceFact`
  producer (ADR 0011 "replaces"). The write path stays bus-free: a check-in
  commits with the relay, consumer, Kafka and Redis all down (outbox-relay
  AC12's write guarantee survives).
- **Frames and projection are contract-frozen.** `contracts/realtime/board.v1.json`,
  `RealtimeService`'s snapshot/stream logic, full-row idempotent upserts,
  recounted counts, scope filtering and the cross-workday forwarding rule are
  untouched. At most one delta per attendance transition (ADR 0011).
- **Facts derive from the event alone (proposed).** `attendance.checked_in` →
  one upsert (`status` from `work_mode`, `since`/`occurred_at` from the
  envelope's `occurred_at`, `late` + `minutes_late` from the payload);
  `attendance.checked_out` → one upsert (`status` `checked_out`, `late` +
  `minutes_late` for the closed record). The consumer never reads PostgreSQL.
- **`attendance.late`, `employee.*` and every other event type produce no wall
  frames.** The `late` envelope remains the lateness fact for Notifications/
  Analytics; the checked-in fact carries the wall's minutes (see D1).
- **Delivery to the wall stays transient, at-most-once.** Consumer, Redis or
  broker failures degrade latency only; missed deltas recover via the client's
  ~60s snapshot refresh and reconnect resync. PostgreSQL is the only truth.
- **The consumer subscribes from `latest` (proposed).** The wall shows "what
  changed now", never history: a consumer start does not replay the topic into
  stale upserts, and downtime gaps close via snapshot refresh. Kafka-side
  delivery is at-least-once and harmless — a redelivered envelope produces an
  identical upsert.
- **The ≤2s product metric survives the relay hop.** Measured from the check-in
  HTTP response to the row on the wall, <2s under gate defaults — the relay
  poll and the consumer's fetch wait are inside this budget.
- **Tenant binding is derived.** Every fact's `tenant_id` comes from its
  envelope and only ever reaches `realtime:tenant:{tenant_id}`; frames carry
  identifiers and state only (never names, emails, notes); logs are ids-only
  (`.ai/security.md`).

## Scope

- Extend the `contracts/events/attendance/` v1 payloads per D1 (proposed:
  `attendance-checked-in.v1.json` gains `minutes_late`;
  `attendance-checked-out.v1.json` gains `late` + `minutes_late`) and the
  Phase-1 builders (`apps/api/app/domains/attendance/events.py`) to fill them;
  `.ai/domain.md`'s event catalog adjusts its consumer list (`attendance.late`
  drops Realtime; `attendance.checked_in`/`checked_out` gain it).
- Remove the presence publish from `AttendanceService`
  (`_publish_presence` and its `PresencePublisher` wiring in
  `apps/api/app/dependencies.py`). `app/shared/presence.py`'s ports and
  `RedisPresencePublisher`/`RedisPresenceStream` stay — the consumer reuses the
  publisher as its output adapter.
- New `workers/realtime/` consumer mirroring `workers/relay/`'s wiring
  (ADR 0012 decision 10: `apps/api` uv environment, `python -m
  workers.realtime.main`, `make dev-realtime`): consume `workpulse.events`,
  filter `attendance.checked_in`/`attendance.checked_out`, map envelope →
  `PresenceFact`, publish to the tenant channel; graceful shutdown; ids-only
  logs.
- Gate: the ≤2s proof through the full hop (real relay + consumer +
  kafka-test + redis-test + WS subscriber) per D2, plus consumer mapping,
  idempotence, tenant-isolation and failure tests.
- ADR 0013 recording the input swap and the payload extension — written before
  the consumer code (workflow rule 4); spec status and `tasks/TODO.md` ticked at
  record time.

## Out of scope

- No web/UI changes and no protocol change: `contracts/realtime/board.v1.json`
  untouched, no new API routes, `make gen-api` diff-free.
- Wall effects for `employee.*` (absent rows, scope changes) — snapshot and lazy
  scope refresh only, exactly as today.
- Durable replay or per-connection backfill for the wall (Redis Streams etc.).
- Any other new consumer of the bus (Notifications is its own spec).
- Topic partitioning/scale-out (ADR 0012: one partition, key `data.employee_id`).
- Changing the `attendance.late` event's meaning or the envelope format.

## Surfaces & files

| Surface | Location |
| --- | --- |
| Events (contracts) | `contracts/events/attendance/attendance-checked-in.v1.json` · `attendance-checked-out.v1.json` (payload fields per D1) |
| Events (builders) | `apps/api/app/domains/attendance/events.py` |
| API domain | `apps/api/app/domains/attendance/service.py` (presence publish removed) |
| API wiring | `apps/api/app/dependencies.py` (publisher wiring out) |
| Workers | `workers/realtime/` (new — `main.py` + consumer/mapping, mirroring `workers/relay/`) |
| Worker settings | `apps/api/app/config.py` (consumer knobs, if any) |
| API tests | `apps/api/tests/unit` (mapping) · `tests/contract` (payload schemas) · `tests/integration` (hop proofs vs kafka-test + redis-test) |
| E2E | `tests/e2e/command-center.spec.ts` (shape per D2) |

## Roles & access

None new — no route is added or changed. Existing role/tenant tests stay green;
a denied check-in (403) writes no outbox row and therefore produces no wall
delta (outbox-relay AC4 is untouched by the swap).

## API contract

None. The write path and every request/response shape are unchanged
(`make gen-api` diff-free).

## Events

Envelope, topic and keying unchanged (v1 envelope, `workpulse.events`, key
`data.employee_id`, ADR 0012). Payload changes (per D1 — proposed):

- `attendance.checked_in` gains **`minutes_late`: integer ≥ 0 | null** — whole
  minutes past `shift.start_time + grace_minutes` when `late` is true (the same
  value the board shows); null when not late. Existing fields unchanged.
- `attendance.checked_out` gains **`late`: boolean** and **`minutes_late`:
  integer ≥ 0 | null** — the closed record's immutable `late` flag and its
  minutes (recomputed at emit time per ADR 0011's read/publish rule). Existing
  fields unchanged.
- New fields are optional in the schemas so pre-extension stored rows and topic
  messages still validate; the builders always fill them. The consumer defaults
  a missing `minutes_late`/`late` to the same values the absence implies today
  (`null` / `false`).
- `attendance.late`, `attendance.checked_in`'s and `attendance.checked_out`'s
  existing fields, and the envelope schema do not move.

## Edge cases

- A late check-in emits two envelopes in one transaction (`checked_in` then
  `late`, ordered): exactly one wall frame — from `checked_in`, carrying
  `minutes_late` (the `late` envelope is filtered).
- Consumer restart mid-stream: offsets advance only after a successful publish;
  at-least-once redelivery yields an identical upsert. `latest` means a fresh
  consumer never floods the wall with historical transitions.
- Relay down: rows stay unpublished, wall deltas stop, writes unaffected;
  after recovery the next transitions flow again (missed ones close via
  snapshot refresh).
- Redis down at the consumer's publish: swallowed with an ids-only log and the
  offset still advances — the transient contract (at-most-once toward the wall),
  identical to today's best-effort publish.
- Worker down at check-in time: the delta is lost by contract; the write path
  and `GET /attendance/board` are unaffected; the client's refresh closes the
  gap.
- Latency tail: relay poll wait + consumer fetch wait must sum well under 2s;
  gate defaults are part of the proof — if the proof flakes, the defaults are
  tuned, never the metric.
- Malformed or foreign topic messages: unknown `event_type` → skipped; an
  unparseable envelope → skipped with an ids-only log (event_id/error type only,
  never payload contents); the consumer never crashes on one bad message.
- Replay of pre-extension rows (fields absent): defaults as above; the `latest`
  subscription means these reach the wall only in a crash-window mid-upgrade.
- Cross-workday deltas (timezone/UTC rollover): forwarded with their own date,
  unchanged.
- Re-check-in after check-out: two transitions, two frames, newest row wins —
  unchanged.

## Acceptance criteria

- [ ] Workers (mapping): given an `attendance.checked_in` envelope on
  `workpulse.events`, when the realtime consumer processes it, then exactly one
  `PresenceFact` reaches `realtime:tenant:{tenant_id}` with `status`
  `present`/`remote` from `work_mode`, `since` = envelope `occurred_at`,
  `reason` `checked_in`, and `late`/`minutes_late` as recorded — and no other
  frame for that transition.
- [ ] Workers (check-out mapping): given `attendance.checked_out`, then one fact
  with `status` `checked_out` and the closed record's `late`/`minutes_late`;
  the row equals the REST board row for that employee (failure case: a payload
  missing a required field is skipped with an ids-only log — never a crash,
  never a partial fact).
- [ ] Workers (filtering): `attendance.late`, `employee.*` and unknown event
  types produce no facts (at most one delta per transition).
- [ ] API (swap): check-in and check-out commit with no presence publisher
  wired — the outbox row is the only live-update output (the domain's publisher
  call sites are gone; producer-side presence tests move to consumer mapping
  tests).
- [ ] Events (contract): the builders' envelopes validate against the updated v1
  schemas with the new fields filled; envelopes without the new fields still
  validate (optional); the persisted-envelope sweep (outbox-relay AC6) stays
  green; `.ai/domain.md`'s catalog consumer lists match the swap.
- [ ] Integration (≤2s through the relay hop): given the relay and consumer
  running against kafka-test + redis-test, when a check-in returns HTTP 201,
  then a WS subscriber receives the `presence.upsert` row **<2s** after the
  response, and the row equals the REST board row (late badge + minutes
  included).
- [ ] Integration (idempotence): redelivering the same envelope produces an
  identical upsert.
- [ ] Integration (tenant isolation): a tenant-A envelope never appears on
  tenant B's channel or frames.
- [ ] Integration (degradation): with relay and consumer stopped, check-in and
  check-out still commit and `GET /attendance/board` is correct while no wall
  delta arrives; with the hop restored the next transition flows again.
- [ ] E2E (web): zero web diffs; the command-center suite stays green, its live
  <2s assertion runs through the full hop (D2) and the reconnect/snapshot tests
  are unchanged.
- [ ] Gate: `make check` green in the check-e2e shape chosen by D2.

## Decisions needed

1. **D1 — how `late`/`minutes_late` reach the wall.** A faithful `PresenceFact`
   needs `minutes_late` at check-in and `late` + `minutes_late` at check-out;
   today's payloads carry `late` on `checked_in` only and neither on
   `checked_out`. Options: (a) **extend the v1 payloads additively** — builders
   fill the new optional fields, schemas update in the same change, no version
   bump (no external consumers exist; pre-extension rows stay valid); keeps the
   consumer stateless and the frames exact; `attendance.late` keeps its
   Notifications/Analytics audience but drops Realtime from `.ai/domain.md`'s
   consumer list; (b) the consumer enriches from PostgreSQL (a read per event;
   the event stops being the fact); (c) a v2 payload bump with dual-publish
   (heavy while nothing consumes v1). **Proposed default: (a).**
2. **D2 — where the ≤2s metric is re-proven.** Post-swap, the browser live-delta
   test needs the full hop (Kafka + relay + realtime worker + Redis) in the e2e
   harness, which outbox-relay deliberately kept Kafka-free. Options:
   (a) **run the full hop in `check-e2e`** — the existing two-tab <2s test keeps
   proving the metric on the real UI through the relay; the gate gains broker +
   worker startup; (b) prove <2s through the hop as a real integration test
   (kafka-test + redis-test + relay + consumer + WS client) in `check-api`, and
   relax the browser test to the degradation contract (snapshot + reconnect
   tests stay; the live-flip assertion moves down a layer) — lean gate, metric
   proven at the process boundary; (c) both proofs. **Proposed default: (a)** —
   the metric is a user-facing promise on a browser surface, and the repo has
   precedent for real brokers in the gate (outbox-relay decision 4).

## Plan

_Not planned yet._
