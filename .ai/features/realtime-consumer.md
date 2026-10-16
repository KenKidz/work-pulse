# Feature: Realtime consumer (the ADR 0011 input swap)

- **Status:** approved
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
- **Facts derive from the event alone.** `attendance.checked_in` →
  one upsert (`status` from `work_mode`, `since`/`occurred_at` from the
  envelope's `occurred_at`, `late` + `minutes_late` from the payload);
  `attendance.checked_out` → one upsert (`status` `checked_out`, `late` +
  `minutes_late` for the closed record). The consumer never reads PostgreSQL
  (D1).
- **`attendance.late`, `employee.*` and every other event type produce no wall
  frames.** The `late` envelope remains the lateness fact for Notifications/
  Analytics; the checked-in fact carries the wall's minutes (see D1).
- **Delivery to the wall stays transient, at-most-once.** Consumer, Redis or
  broker failures degrade latency only; missed deltas recover via the client's
  ~60s snapshot refresh and reconnect resync. PostgreSQL is the only truth.
- **The consumer subscribes from `latest`.** The wall shows "what
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
- Gate: `check-e2e` starts the full hop (kafka-test + relay + realtime worker
  + Redis) so the browser two-tab <2s test is re-proven through the relay
  (D2); `check-api` keeps real-broker consumer proofs (mapping, idempotence,
  tenant isolation, failures) against kafka-test + redis-test.
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
`data.employee_id`, ADR 0012). Payload changes (per D1):

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

## Decisions (PO, 2026-10-16)

1. **D1 — `late`/`minutes_late` reach the wall via additive v1 payload
   extensions.** `attendance-checked-in.v1.json` gains optional `minutes_late`;
   `attendance-checked-out.v1.json` gains optional `late` + `minutes_late`;
   the builders fill them, schemas update in the same change, no version bump
   (no external consumers exist and pre-extension rows stay valid because the
   fields are optional). Rationale: the consumer stays stateless, the frames
   stay exact, and "one fact, one event" (product goal 2) means the event must
   carry the fact's fields. Rejected: DB enrichment (event stops being the
   fact), degraded rows (WS would disagree with REST until refresh).
   `.ai/domain.md`'s consumer list follows: `attendance.late` drops Realtime,
   `checked_in`/`checked_out` gain it.
2. **D2 — the ≤2s metric is re-proven in the browser e2e through the full
   hop.** `check-e2e` starts kafka-test + the relay + the realtime worker; the
   existing two-tab live-delta test in `tests/e2e/command-center.spec.ts` keeps
   its <2s assertion, now measured through the relay hop. Rationale: the metric
   is a user-facing promise on a browser surface and outbox-relay decision 1
   asked for it "re-proven through the relay hop on its own"; the repo already
   accepts real brokers in the gate (outbox-relay decision 4). Consequence
   recorded: outbox-relay AC12's "e2e flows pass without Kafka" narrows to the
   write path and snapshot/resync flows — the wall's live-delta spec now
   requires the hop by design (writes and `GET /attendance/board` remain
   bus-free). Rejected: integration-only proof (browser live path would lose
   coverage), both proofs (gate cost).

## Plan

> Produced by `/plan`, 2026-10-16. Build order: **ADR → event contracts (producer
> side) → worker → e2e hop wiring → the swap (presence publish removed) →
> real-broker hop proofs → record.** The swap (T5) lands *after* the gate already
> starts the hop (T4) so `make check` stays green after every task: between T4 and
> T5 the in-process publish and the consumer briefly dual-feed the wall (identical
> full-row upserts — idempotent by contract, R9), and from T5 on the bus is the
> only input. No web surface is touched anywhere.

### Acceptance criteria → changes (restated)

| # | Criterion (restated) | Satisfied by |
| --- | --- | --- |
| AC1 | One `attendance.checked_in` envelope → exactly one `PresenceFact` on `realtime:tenant:{tenant_id}` (`status` from `work_mode`, `since` = envelope `occurred_at`, `reason` `checked_in`, `late`/`minutes_late` as recorded) and no other frame | T3 — mapping + unit tests; T6 — exactly-one proof on the channel |
| AC2 | `attendance.checked_out` → one fact (`status` `checked_out`, closed record's `late`/`minutes_late`), row equals the REST board row; a payload missing a required field is skipped with an ids-only log — never a crash, never a partial fact | T3 — mapping + skip tests; T6 — check-out parity vs `GET /attendance/board` |
| AC3 | `attendance.late`, `employee.*`, unknown event types produce no facts | T3 — filter tests |
| AC4 | Check-in/out commit with no presence publisher wired — the outbox row is the only live-update output; producer-side presence tests move to consumer mapping tests | T5 — `_publish_presence` + `PresencePublisher` wiring removed; tests moved/rewritten |
| AC5 | Builders' envelopes validate against the updated v1 schemas with new fields filled; envelopes without them still validate (optional); persisted-envelope sweep (outbox-relay AC6) green; `.ai/domain.md` catalog consumer lists match the swap | T2 — schema extension + builder fills + contract cases + catalog edit |
| AC6 | Relay + consumer vs kafka-test + redis-test: check-in HTTP 201 → WS `presence.upsert` row **<2s** after the response, row equals the REST board row (late badge + minutes included) | T6 — full-hop latency/parity proof; re-proven in-browser by the AC10 suite under D2 |
| AC7 | Redelivering the same envelope produces an identical upsert | T6 — redelivery proof (full-row idempotent upsert) |
| AC8 | A tenant-A envelope never appears on tenant B's channel or frames | T6 — cross-tenant proof |
| AC9 | Relay + consumer stopped: check-in/out still commit, `GET /attendance/board` correct, no wall delta; hop restored → next transition flows again | T6 — degradation/recovery proof |
| AC10 | Zero web diffs; command-center suite green with its live <2s assertion running through the full hop (D2); reconnect/snapshot tests unchanged | T4 — `check-e2e` starts kafka-test + relay + realtime; `tests/e2e/command-center.spec.ts` byte-identical |
| AC11 | `make check` green in the check-e2e shape chosen by D2 | T7 — full gate at record time |

### Repo research findings the plan relies on

- **F1 — the swap's seam already exists.** `AttendanceService` takes a
  `PresencePublisher` port (`app/shared/presence.py`); the only wiring is
  `dependencies.py:136` (`presence=RedisPresencePublisher(get_redis())`).
  `AttendanceMutation` carries `record` + `events` only and routers return
  `.record`, so nothing HTTP-facing changes — `make gen-api` diff-free holds.
- **F2 — `since` parity is exact, not approximate.** `check_in` passes the same
  `now` to `repo.add(checked_in_at=now, …)` and
  `attendance_checked_in(occurred_at=now, …)` (service.py:311–339); check_out
  mirrors it. Mapping `since` = envelope `occurred_at` therefore equals the REST
  board row's `since` (`checked_in_at` / `checked_out_at`) — AC2/AC6 parity holds
  (caveat R5).
- **F3 — contract tests tolerate a schema/builder split.**
  `test_schema_files_match_builder_cases` compares schema-file *sets*, not
  properties, and `test_late_payload_is_identical_to_its_checked_in_fact`
  compares only `attendance_id`/`employee_id`/`workday` — both survive the
  extension untouched.
- **F4 — optional vs nullable convention.** Event payload schemas express
  optional purely via `required`-list membership and have no nullable keys today;
  `"type": ["integer","null"]` exists only in `contracts/realtime/board.v1.json`.
  The extension (D1) introduces nullable unions into `contracts/events/` —
  recorded in ADR 0013.
- **F5 — worker wiring precedent is complete.** `dev-relay` runs
  `PYTHONPATH="../..:." uv run python -m workers.relay.main` from `apps/api`;
  `apps/api/pyproject.toml` sets `pythonpath = [".", "../.."]`; `check-api`'s
  ruff/pyright already cover `../../workers`. The realtime worker inherits all
  three unchanged. ADR 0012 decision 10 promised to revisit this wiring "when a
  second worker arrives" — this is that moment (ADR 0013 records the revisit).
- **F6 — no consumer precedent.** The relay is produce-only; `AIOKafkaConsumer`
  group/commit semantics are new code (`aiokafka` already a dependency). The
  relay loop (`workers/relay/relay.py`) supplies the shape: Protocol ports, stop
  event, backoff, ids-only logs, `_safe_error` redaction.
- **F7 — Playwright cannot host the workers.** `webServer` readiness is a polled
  URL/port; the workers listen on nothing. Worker lifecycle therefore lives in
  the Makefile/script layer with log-line readiness (T4), while `playwright.config.ts`
  keeps only uvicorn + next dev.
- **F8 — the e2e latency assertion is wall-clock** (`Date.now()` around
  `toBeVisible({ timeout: 2_000 })` after the check-in response,
  `tests/e2e/command-center.spec.ts:91–106`). It stays byte-identical; only the
  processes feeding it change.
- **F9 — integration harness precedent.** `tests/integration/test_outbox_relay.py`
  runs the real relay as an asyncio task against kafka-test (:9093, forced in
  `tests/conftest.py`) with topic-end positioning; `test_realtime_bus.py` has
  race-free `_wait_subscribed`. The hop tests reuse both, plus per-test unique
  consumer groups.
- **F10 — producer-side presence tests inventory.** `tests/unit/attendance/test_presence_facts.py`
  (7 tests): 5 pure fact-mapping assertions re-appear as consumer mapping tests;
  `test_minutes_late_is_recomputed_from_the_records_shift` +
  `test_check_out_without_a_resolvable_shift_has_no_minutes` re-target the
  emitted envelopes (the service keeps that math for the payloads);
  `test_a_raising_publisher_never_fails_a_capture` is superseded by T3/T6.
  `test_outbox_appends.py::test_outbox_failure_propagates_before_presence_and_audit`
  re-anchors to outbox-before-audit. Constructor call sites passing
  `presence=`: `tests/contract/test_realtime_frames.py`,
  `tests/integration/test_attendance_api.py`, `tests/integration/test_realtime_api.py`
  (incl. `test_snapshot_is_served_with_a_dead_bus`).

**Deviations from the spec's assumptions**

- **V1 — `.ai/domain.md`'s `checked_in`/`checked_out` rows already name
  Realtime** (catalog L81–82), so D1's "checked_in/checked_out gain it" is
  already true; the only row that moves is `attendance.late` → `Notifications,
  Analytics`. AC5 is met when the three rows match the post-swap world.
- **V2 — one line outside the Surfaces & files table:** `workers/relay/main.py`
  gains a `relay_started` log line (T4 readiness twin of the consumer's
  `realtime_consumer_started`). Ids-only, no behavior change.

### Ordered tasks

**T1 — ADR 0013 (docs first, before consumer code).**
New `.ai/decisions/0013-realtime-consumer-input-swap.md` recording:
(1) the input swap — the realtime consumer is the sole `PresenceFact` producer,
closing ADR 0011's promise, write path stays bus-free; (2) the additive v1
payload extension per D1 (`minutes_late` on checked-in; `late` + `minutes_late`
on checked-out), no version bump, plus the nullable-field convention
(`"type": ["integer","null"]`, optional via `required` — F4); (3) consumer
semantics — stable group `workpulse-realtime`, `auto_offset_reset="latest"`
(first start at tip, never replays history), manual offset commit after each
handled message (publish attempt complete; swallowed Redis failures still
advance), at-least-once from Kafka / at-most-once toward the wall, snapshot
refresh closes gaps (R2); (4) the D2 gate shape and the recorded narrowing of
outbox-relay AC12 to the write path + snapshot/resync flows; (5) the ADR 0012
decision-10 revisit (F5): keep `workers/*` in the `apps/api` uv env with
`PYTHONPATH="../..:."` for the second worker; revisit at the third worker or
when worker dependency sets diverge.
*Tests:* none (docs-only).
*Verify:* diff is docs-only; `make check-api` unaffected.

**T2 — Event contract extension, producer side (contracts before producers).**
- `contracts/events/attendance/attendance-checked-in.v1.json` — add optional
  `"minutes_late": {"type": ["integer","null"], "minimum": 0}` (kept out of
  `required`), `additionalProperties: false` unchanged.
- `contracts/events/attendance/attendance-checked-out.v1.json` — add optional
  `"late": {"type": "boolean"}` and `"minutes_late": {"type": ["integer","null"], "minimum": 0}`.
- `apps/api/app/domains/attendance/events.py` — `attendance_checked_in` gains
  `minutes_late: int | None`; `attendance_checked_out` gains `late: bool` +
  `minutes_late: int | None`; builders always emit the keys (null when not late
  / unresolvable). `attendance_late` and the envelope builder untouched.
- `apps/api/app/domains/attendance/service.py` — check_in's builder call passes
  `minutes_late=minutes_late if late else None` (same wire rule as the fact
  today, service.py:369); check_out's passes `late=closed.late` and the
  emit-time `_minutes_late(closed, shift, …)` value already computed for the
  fact (service.py:439–457). Presence publish left in place (removed in T5).
- `apps/api/tests/contract/test_attendance_events.py` — builder cases fill the
  new fields; new cases: pre-extension payloads (new keys stripped) still
  validate; wrong types rejected (`minutes_late` negative/string, `late`
  non-bool); PII sweep and `test_late_payload_is_identical_to_its_checked_in_fact`
  unchanged (F3). Direct builder call sites in
  `tests/unit/attendance/test_outbox_appends.py`,
  `tests/integration/test_outbox_attendance.py`,
  `tests/integration/test_outbox_producers.py` updated for the new kwargs.
- `.ai/domain.md` catalog L81–83 — `attendance.late` row's Main consumers →
  `Notifications, Analytics` (V1).
*Tests:* the contract cases above.
*Verify:* `make check-api` green — incl.
`test_outbox_contracts.py::test_persisted_envelopes_validate_against_their_contracts`
(outbox-relay AC6 sweep).

**T3 — Realtime worker (`workers/realtime/`).**
- `workers/realtime/__init__.py` — layer docstring mirroring `workers/relay/`.
- `workers/realtime/mapping.py` — pure `map_envelope(envelope) -> PresenceFact |
  None`: filter `event_type` ∈ {`attendance.checked_in`, `attendance.checked_out`}
  with `version == 1` (`attendance.late`, `employee.*`, unknown → `None`,
  silent); `status` = `remote`/`present` from `work_mode`; `since`/`occurred_at`
  = envelope `occurred_at`; `late`/`minutes_late` from the payload, defaulting
  `false`/`null` when absent (pre-extension envelopes); any missing
  fact-required field or unparseable value → `None` + skip signal — never a
  partial fact, never raises.
- `workers/realtime/consumer.py` — `RealtimeConsumer` with relay-style Protocol
  ports; loop: `getmany(timeout_ms=…)` → per message: parse → map → publish →
  manual offset commit. Output adapter is the shared `PresencePublisher`
  (`RedisPresencePublisher`, "implementations never raise"); a raising adapter is
  still swallowed ids-only (`consumer_publish_failed`, error type only) and the
  offset advances — the transient contract. Malformed envelopes skipped with
  `consumer_message_skipped` (event_id/error type only, never payload contents).
  Graceful SIGINT/SIGTERM between messages; broker errors → ids-only log +
  backoff, loop never dies. A `started` asyncio.Event is set after the first
  completed fetch round (readiness for T4/T6 — closes the `latest`-skip race R6).
- `workers/realtime/main.py` — `python -m workers.realtime.main`, mirroring
  `workers/relay/main.py`: settings → `AIOKafkaConsumer` (`group_id=
  settings.realtime_consumer_group`, `auto_offset_reset="latest"`,
  `enable_auto_commit=False`) + `RedisPresencePublisher(get_redis())` → signal
  handlers → log `realtime_consumer_started` (after the first fetch round) /
  `realtime_consumer_stopped`; only configuration errors exit loudly.
- `apps/api/app/config.py` — `realtime_consumer_group: str = "workpulse-realtime"`,
  `realtime_poll_seconds: float = 1.0` (getmany max wait — returns early on
  data), `realtime_backoff_base_seconds: float = 1.0`,
  `realtime_backoff_max_seconds: float = 30.0`.
- `Makefile` — `dev-realtime` mirroring `dev-relay`:
  `cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.realtime.main`.
*Tests:* `apps/api/tests/unit/workers/test_realtime_mapping.py` (AC1/AC2/AC3 +
failure cases: missing required field → skip; pre-extension defaults; `late`/
`employee.*`/unknown filtered; a late check-in maps exactly one fact from
`checked_in`); `apps/api/tests/unit/workers/test_realtime_consumer.py`
(stub-source loop in the `tests/unit/outbox/test_relay.py` style: offset advances
only after the publish attempt; raising publisher swallowed + offset advances;
malformed message skipped and loop continues; stop finishes in-flight then
exits; backoff grows and caps). No brokers required.
*Verify:* `make check-api` (ruff/pyright already cover `../../workers`).

**T4 — E2E full-hop gate shape (D2).**
- `workers/relay/main.py` — +1 ids-only `relay_started` log after
  `publisher.start()` (V2).
- `tests/e2e/full-hop.sh` (new) — start `workers.relay.main` +
  `workers.realtime.main` with `KAFKA_BOOTSTRAP_SERVERS=localhost:9093` (all
  other settings default → dev Postgres :5432 and dev Redis :6379, the same
  services Playwright's uvicorn uses), wait deterministically for the
  `relay_started` / `realtime_consumer_started` lines (no sleeps), run
  `npx playwright test`, tear both down on exit (trap).
- `Makefile` — `check-e2e` gains `kafka-test-up` and its recipe becomes
  `bash tests/e2e/full-hop.sh`.
- `tests/e2e/command-center.spec.ts` + `playwright.config.ts` — untouched.
*Tests:* none new — the existing suite must pass in the new shape (AC10). Until
T5 the browser cannot tell the two wall inputs apart (R9).
*Verify:* `make check-e2e` green.

**T5 — The swap: presence publish removed (AC4).**
- `apps/api/app/domains/attendance/service.py` — delete `_publish_presence`
  (L678–695) and both call sites (check_in L360–374, check_out L444–457); drop
  `presence` from `AttendanceService.__init__`. `_presence_status` and
  `_minutes_late` stay — they feed the envelopes and board rows.
- `apps/api/app/dependencies.py` — `get_attendance_service` drops
  `presence=RedisPresencePublisher(get_redis())` (L136).
- `app/shared/presence.py`, `RedisPresencePublisher`, `RedisPresenceStream` —
  untouched (the worker reuses the publisher as its output adapter; the wall
  still consumes the stream).
- Tests (F10): delete `tests/unit/attendance/test_presence_facts.py` (its 5
  mapping assertions live in T3's mapping tests; its 2 minutes-late tests
  re-target the emitted envelopes in `tests/unit/attendance/`);
  `test_outbox_appends.py::test_outbox_failure_propagates_before_presence_and_audit`
  → `…_before_audit`; drop `presence=` from constructor calls in
  `tests/contract/test_realtime_frames.py`, `tests/integration/test_attendance_api.py`,
  `tests/integration/test_realtime_api.py`; re-scope
  `test_snapshot_is_served_with_a_dead_bus` (snapshot correctness needs no bus —
  live-path dead-bus behavior is T6's degradation proof).
*Tests:* the moved/adjusted unit tests above.
*Verify:* `make check` — `check-api` green **and** `check-e2e` green with the
wall fed by the hop alone (this is where "the bus is the wall's only input"
becomes browser-proven).

**T6 — Real-broker hop proofs (kafka-test + redis-test).**
New `apps/api/tests/integration/test_realtime_consumer.py`, reusing
`test_outbox_relay.py`'s harness (real `OutboxRelay` as an asyncio task,
topic-end positioning) and `test_realtime_bus.py`'s `_wait_subscribed`; the real
`RealtimeConsumer` runs as a task with a per-test unique `group_id`, awaited on
its `started` event before the action.
*Tests:*
- `test_check_in_reaches_the_ws_row_under_2s_and_matches_the_rest_board` (AC6) —
  check-in 201 → `time.monotonic()` → WS `presence.upsert` <2s → row equals
  `GET /attendance/board`; a late check-in case asserts `late` + `minutes_late`
  parity.
- `test_one_transition_produces_exactly_one_fact` (AC1) — a late check-in emits
  `checked_in` + `late` in one transaction; exactly one fact reaches
  `realtime:tenant:{tenant_id}` (the `late` envelope is filtered).
- `test_check_out_fact_matches_the_rest_board_row` (AC2).
- `test_redelivering_the_same_envelope_upserts_identically` (AC7) — same envelope
  twice → identical frames/state; plus a pre-extension envelope (new keys
  absent) → defaulted row.
- `test_tenant_b_never_sees_tenant_a_deltas` (AC8).
- `test_degraded_hop_never_blocks_the_write_path` (AC9) — relay+consumer stopped
  → check-in/out 201, board correct, no frame within a bounded window; hop
  restored → next transition flows <2s.
- `test_bad_topic_messages_never_stall_the_consumer` — garbage bytes,
  `employee.hired`, and an unparseable envelope on the topic → the next good
  transition still arrives; skips logged ids-only.
*Verify:* `make check-api`.

**T7 — Record.**
This spec: AC boxes ticked, `Status: done`. `tasks/TODO.md`: realtime-consumer
row `Now` → `Done` with gate counts. `.ai/features/README.md`: row status →
`done` (README convention: updated in the same change). `.ai/features/outbox-relay.md`:
one parenthetical under AC12 recording the D2 narrowing (write path +
snapshot/resync flows only). Confirm `make gen-api` diff-free and zero
`apps/web` diffs.
*Verify:* `make check` (check-api + check-web + check-e2e in the D2 shape).

### Risks, unknowns, ADR-worthy decisions

- **R1 — latency tail lives in the relay's idle poll.** Worst case ≈
  `relay_poll_seconds` (1.0s sleep after a drained batch) + consumer fetch
  (returns early on data) + WS fan-out — inside the 2s budget under gate
  defaults. If CI flakes, tune `relay_poll_seconds` / `realtime_poll_seconds`
  in the gate — never the metric (spec edge case).
- **R2 — `latest` + group semantics interpretation.** A stable group resumes its
  committed offsets on restart (at-least-once; messages published during
  downtime may be delivered late — harmless full-row upserts) and a first
  start/no-offset begins at the tip (no history replay). The *contract* remains
  at-most-once toward the wall with snapshot-refresh recovery. If the PO meant
  "every start seeks to end", that trades away at-least-once redelivery — which
  AC7's "redelivered envelope" premise requires. Recorded in ADR 0013; flagged,
  not chosen.
- **R3 — nullable payload fields are new to `contracts/events/`** (F4). The
  convention is recorded in ADR 0013; the omit-when-null alternative is
  rejected by D1's "`integer ≥ 0 | null`" + "the builders always fill them".
- **R4 — the e2e gate now needs kafka-test + two worker processes (D2).**
  outbox-relay AC12's "e2e flows pass without Kafka" narrows to the write path
  and snapshot/resync flows; the write-path guarantee keeps its proof in
  check-api's degradation test (AC9). The full-hop runner's log-line readiness is
  new gate infrastructure — a readiness bug would look like a product flake, so
  readiness is deterministic (log after `publisher.start()` / after the first
  fetch round), never sleep-based.
- **R5 — WS↔REST row parity assumes no shift edit between transition and
  comparison.** `_minutes_late` is recomputed at REST read time
  (service.py:162–176 — accepted drift since Phase 2) while the fact carries
  the emit-time value. AC2/AC6 parity assertions run without shift edits; the
  drift itself is pre-existing and out of scope.
- **R6 — `latest` skip race.** If the consumer's first fetch round lands after a
  transition's envelope was published (fresh group), the delta is skipped by
  design. The `started` event + T4's log-line wait close it for tests; in
  production the snapshot refresh is the contract.
- **R7 — signature ripple.** Builder kwargs (T2) touch direct builder call sites
  in contract/unit/integration tests; `presence=` constructor kwargs (T5) touch
  four known files (F10). Mechanical, covered per task.
- **R8 — `attendance.late` stays on the topic** for Notifications/Analytics; if
  the realtime filter failed to skip it, a late check-in would produce two wall
  frames and violate "at most one delta per transition". Covered by T3 filter
  tests + T6's exactly-one proof.
- **R9 — transient dual input (T4→T5 build window).** In-process publish and the
  consumer briefly both feed the wall; identical full-row upserts make it
  harmless and T5 ends it. Noted so a mid-build reviewer doesn't read it as the
  final state.
- **ADR-worthy:** ADR 0013 (T1) covers the swap, the payload extension +
  nullable convention, the consumer delivery semantics (R2), the D2 gate shape
  and the ADR 0012 decision-10 wiring revisit. Nothing else rises to an ADR.

### Test strategy (gates × failure cases)

| Gate | What runs | New/changed tests | Failure cases covered |
| --- | --- | --- | --- |
| `make check-api` | ruff + pyright over `apps/api` + `workers/`; pytest (unit, contract, integration vs postgres-test/kafka-test/redis-test) | new `tests/unit/workers/test_realtime_mapping.py` + `test_realtime_consumer.py`; updated `tests/contract/test_attendance_events.py`; updated `tests/unit/attendance/*` (presence tests moved, outbox ordering re-anchored); constructor call-site updates in `tests/contract/test_realtime_frames.py`, `tests/integration/test_attendance_api.py`, `test_realtime_api.py`; new `tests/integration/test_realtime_consumer.py` | mapping (AC1–AC3: missing required field → skip, never a partial fact/crash; pre-extension defaults `null`/`false`; `late`/`employee.*`/unknown filtered); loop (offset-after-publish, swallowed Redis failure, backoff, graceful stop); contract (new fields optional + type-strict, PII sweep, persisted-envelope sweep); hop (≤2s + REST parity AC6, exactly-one AC1, check-out parity AC2, redelivery AC7, tenant isolation AC8, degradation AC9, bad-message resilience) |
| `make check-web` | typecheck + lint + vitest | none — zero web diffs, `make gen-api` diff-free | unchanged |
| `make check-e2e` | Playwright with kafka-test + relay + realtime worker (D2 shape) | none changed — `tests/e2e/command-center.spec.ts` re-aimed at the full hop | browser <2s live delta through the relay hop (AC6/AC10); reconnect/snapshot tests unchanged (transient contract); cookie-less WS and role-denial suites untouched |
| `make check` | all three | — | AC11 |

Per-domain rules: **tenant isolation** — the fact's `tenant_id` derives from its
envelope and only ever reaches `realtime:tenant:{tenant_id}` (AC8 + the stream's
tenant re-bind); **denied-role case** — no route changes (Roles & access), and a
denied check-in still writes no outbox row, so it produces no wall delta
(outbox-relay AC4 untouched); **audit** — unchanged (sensitive mutations already
audited; the swap removes only the presence publish); **PII** — frames carry
identifiers/state only and consumer logs are ids-only (T3/T6 assert skip logs
never echo payload contents).
