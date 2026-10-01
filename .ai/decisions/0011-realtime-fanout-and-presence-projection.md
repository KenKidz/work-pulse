# 11. Realtime fan-out and the presence projection

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

`.ai/features/realtime-command-center.md` (T1–T6, 2026-10-01) made the product's
core promise real — a check-in reaches the manager's wall in seconds — and
brought Redis and WebSockets into the stack exactly where ADR 0006 phase-gated
them (architecture rule: a technology is adopted with a spec **and** an ADR).
Three choices are expensive to reverse: how deltas reach viewers across
processes, where PresenceState lives, and what Phase 3's durable event backbone
will have to preserve.

Alternatives considered:

- **In-process fan-out (process memory).** Simplest, but forbidden: deployments
  with several API workers must all reach every viewer, and a bus inside one
  process cannot.
- **Redis Streams / durable replay in Phase 2.** Would duplicate the machinery
  Phase 3's transactional outbox + Kafka exists to own; the wall needs
  "what changed now", not "what I missed".
- **PresenceState as Redis rows cache.** A second source of truth beside
  PostgreSQL with its own staleness paths; the projection needs one snapshot
  computation (ADR 0009's board already accepted that cost).

## Decision

- **Fan-out is transient, at-most-once Redis pub/sub** — one channel per tenant
  (`realtime:tenant:{tenant_id}`), JSON `PresenceFact` payloads through ports in
  `app/shared/presence.py` (`PresencePublisher` / `PresenceStream`); domains
  never import Redis (architecture rule 3). Attendance publishes best-effort
  after check-in/check-out — a Redis outage degrades latency (deltas stop, the
  client's ~60s snapshot refresh keeps the board current) and never fails a
  write or invents a fact. Missed deltas are recovered by resyncing a snapshot
  on reconnect; durable replay is Phase 3's outbox + Kafka.
- **PresenceState is a live per-connection projection** — rows map + recounted
  counts, rebuilt from a PostgreSQL snapshot at connect time and kept fresh by
  channel frames. **No Redis rows cache**: PostgreSQL is the source of truth and
  every snapshot recomputes from it (`.ai/features/realtime-command-center.md`;
  the ADR 0009 risk-7 deferral is now realised as "the hot state is the
  connection, not a store"). A read-through Redis cache with TTL is the known
  upgrade — one port swap — if snapshot latency under many viewers ever matters.
- **Pushes are full-row idempotent upserts with fresh counts.** Redelivery is
  harmless and clients never patch partial state; at most one delta per
  attendance transition. Frames carry identifiers and state only — never names,
  emails or notes (`.ai/security.md`, same rule as event payloads) — and are
  versioned in `contracts/realtime/` as **projection** protocols, never in the
  event catalog.
- **`minutes_late` is recomputed at read/publish time** from the record's
  `shift_id` + the shift's start/grace boundary (the `late` flag itself is
  stored and immutable). Editing a shift after a late check-in changes the
  displayed minutes — accepted; the flag never drifts.
- **WS denial is close-before-accept** (zero application frames), resolved from
  the `wp_session` cookie exactly like every other route (ADR 0010). Both denial
  cases surface as the single uvicorn upgrade status **403**; the 401-vs-403
  distinction lives on the REST snapshot (`GET /api/v1/realtime/board`), which
  serves initial render and degraded mode with the same payload as the
  `snapshot` frame.
- **Heartbeat is uvicorn's built-in WS ping/pong** — no application-level
  `ping`/`pong` frames and no lifespan task: each connection subscribes to its
  tenant channel itself (a refcounted in-process hub is the known upgrade path
  for N viewers and would not change the protocol).

## Consequences

- When Phase 3's transactional outbox + Kafka land, the realtime consumer
  replaces the in-process presence-delta port as the **input** to this same
  projection and protocol — `attendance.*` events feed the identical frames with
  no protocol change, and `contracts/realtime/board.v1.json` stays as-is.
- Delivery semantics are transient by contract: no catch-up, no exactly-once,
  no ordering guarantee beyond "fresh snapshot on reconnect". Anything that
  needs history reads PostgreSQL (attendance records) — the wall shows only the
  current workday.
- Redis becomes a latency dependency, never a truth dependency: killing Redis
  degrades live updates only; the REST snapshot keeps serving correct boards
  from PostgreSQL.
- N viewers mean N Redis subscribers on one channel — fine at this scale; the
  refcounted hub (or a rows cache) is the documented upgrade, and neither moves
  data out of the `realtime/` protocol or the presence ports.
- Clock-driven transitions (e.g. `absent` onset at `start + grace`) push
  nothing — they appear at the next ~60s snapshot refresh. The ≤2s product
  metric covers check-in visibility only; the UI copy says the wall refreshes
  itself.
