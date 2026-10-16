# 13. Realtime consumer input swap

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

ADR 0011 promised that "when Phase 3's transactional outbox + Kafka land, the
realtime consumer replaces the in-process presence-delta port as the **input**
to this same projection and protocol — `attendance.*` events feed the identical
frames with no protocol change". The outbox-relay feature (ADR 0012) built the
backbone only and deliberately deferred the swap; `.ai/features/realtime-consumer.md`
is the feature that closes the promise. Until it lands, `AttendanceService`
dual-writes a `PresenceFact` to Redis beside the outbox row, so the wall is not
a projection of the event bus and `.ai/product.md` goal 2 ("one fact, one event
… realtime state is a projection of it") is unmet.

Choices that are expensive to reverse once the consumer runs: what produces
wall facts (the input swap itself), how `late`/`minutes_late` reach the wall
(the event payload's shape), the consumer's delivery/offset semantics, what the
gate starts, and the second worker's wiring.

Alternatives considered:

- **DB enrichment at the consumer** (read PostgreSQL for `late`/`minutes_late`).
  The event stops being the fact's source and the consumer gains a database
  dependency — rejected by D1: "one fact, one event" means the event carries
  the fact's fields.
- **Degraded rows** (frames without minutes until snapshot refresh). WS would
  disagree with the REST board until refresh — rejected; the frames stay exact.
- **`late`/`minutes_late` version bump to v2 payloads.** No external consumers
  exist and optional fields keep pre-extension rows valid; a version bump is
  cost without benefit (see Decision 2).
- **Integration-only latency proof** (or both integration and browser proofs).
  The ≤2s metric is a user-facing promise on a browser surface; outbox-relay
  decision 1 asked for it re-proven through the relay hop. Recording both
  proofs doubles gate cost — rejected (D2).

## Decision

1. **The bus is the wall's only input.** The realtime consumer
   (`workers/realtime/`) becomes the sole `PresenceFact` producer;
   `AttendanceService`'s presence publish and its `PresencePublisher` wiring
   are removed. The write path stays bus-free: a check-in commits with the
   relay, consumer, Kafka and Redis all down (outbox-relay AC12's write
   guarantee survives the swap). `app/shared/presence.py`'s ports and
   `RedisPresencePublisher`/`RedisPresenceStream` are untouched — the consumer
   reuses the publisher as its output adapter, and `contracts/realtime/board.v1.json`
   plus `RealtimeService`'s projection stay contract-frozen.
2. **Additive v1 payload extension (no version bump).**
   `attendance-checked-in.v1.json` gains optional `minutes_late`
   (`{"type": ["integer","null"], "minimum": 0}`); `attendance-checked-out.v1.json`
   gains optional `late` (`{"type": "boolean"}`) and `minutes_late`. The
   Phase-1 builders always fill the keys (null when not late / unresolvable);
   schemas keep `additionalProperties: false` and the fields stay out of
   `required`, so pre-extension stored rows and topic messages still validate
   and the consumer defaults a missing `minutes_late`/`late` to `null`/`false`.
   **Nullable-field convention** (new to `contracts/events/`): optional via
   `required`-list membership, nullable via `"type": ["integer","null"]` — the
   same shape `contracts/realtime/board.v1.json` already uses. The
   `attendance.late` envelope, the envelope format and the existing payload
   fields do not move.
3. **Consumer semantics.** Stable group `workpulse-realtime`
   (`settings.realtime_consumer_group`) with `auto_offset_reset="latest"`: a
   first start begins at the topic tip — the wall shows "what changed now",
   never history — while a restart resumes committed offsets (at-least-once
   from Kafka; messages published during downtime may arrive late as harmless
   identical full-row upserts). Offsets commit **manually after each handled
   message** — after the publish *attempt* completes; a swallowed Redis failure
   (`RedisPresencePublisher` never raises by contract; a raising adapter is
   likewise swallowed with an ids-only log) still advances the offset. Net
   contract: at-least-once from Kafka, **at-most-once toward the wall**;
   missed deltas recover via the client's ~60s snapshot refresh and reconnect
   resync (ADR 0011's transient delivery). If every start seeking to end were
   wanted instead, that trades away at-least-once redelivery — which the
   redelivery premise requires — so "latest = first start at tip" is the
   recorded reading (spec risk R2).
4. **Gate shape (D2).** `make check-e2e` starts kafka-test + the outbox relay
   + the realtime worker; the existing two-tab browser <2s live-delta assertion
   in `tests/e2e/command-center.spec.ts` keeps its shape and now runs through
   the full relay hop. **Consequence recorded:** outbox-relay AC12's "e2e flows
   pass without Kafka" narrows to the write path and snapshot/resync flows —
   the wall's live-delta spec requires the hop by design (writes and
   `GET /attendance/board` remain bus-free, proven in `check-api`). Worker
   lifecycle lives in the Makefile/script layer with deterministic log-line
   readiness (`relay_started`, `realtime_consumer_started` — never sleeps);
   `playwright.config.ts` still starts only uvicorn + next dev.
5. **ADR 0012 decision-10 revisit, recorded at the second worker.**
   `workers/*` stays inside the `apps/api` uv environment — `python -m
   workers.<name>.main` from `apps/api` with `PYTHONPATH="../..:."`, the
   `pythonpath = [".", "../.."]` test config and `check-api`'s ruff/pyright
   over `../../workers` all unchanged. A uv workspace or a separate worker
   project is the documented upgrade; revisit at the third worker or when
   worker dependency sets diverge.

## Consequences

- The wall is finally a projection of `workpulse.events`: "one fact, one
  event" (product goal 2) holds end-to-end, and the dual write beside the
  outbox is gone.
- Frames are derived from the event alone — the consumer never reads
  PostgreSQL, so `since`/`occurred_at` parity with the REST board row rests on
  the builder injecting the same clock into the record and the envelope
  (accepted; a shift edit after a transition is pre-existing read-time drift,
  spec risk R5).
- The event payload now carries the wall's lateness fields, so the checked-in
  fact's `late`/`minutes_late` are the emit-time values; `attendance.late`
  remains the lateness fact for Notifications/Analytics and produces no wall
  frame (at most one delta per transition, ADR 0011).
- The e2e gate grows two worker processes and a Kafka dependency; a readiness
  bug in the hop runner looks like a product flake, which is why readiness is
  log-line-deterministic. Gate cost is accepted in exchange for browser-proof
  latency through the real hop.
- The write path's independence from the bus is unchanged and re-proven:
  with the hop down, check-ins commit and `GET /attendance/board` is correct
  while wall deltas stop and later close via snapshot refresh.
- Nullable payload fields are now a `contracts/events/` convention; future
  event extensions follow Decision 2's shape instead of re-litigating it.
- Ids-only logging applies to the consumer as to the relay: skip/failure logs
  carry `event_id` and error type only, never payload contents
  (`.ai/security.md`).
