# 12. Outbox transport and relay

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

`.ai/features/outbox-relay.md` (T1) brings the seven Phase-1 event contracts
(`employee.*` × 4, `attendance.*` × 3) in flight: they are built,
contract-tested and then dropped on the floor today. The fix is the
architecture's own — a transactional outbox (`event_outbox` rows committing in
the same PostgreSQL transaction as the state change and its audit entry) plus a
relay publishing committed envelopes to Kafka at-least-once. Per workflow rule 4
(contract before producer or consumer code) the **transport** contract is
pinned here first; the 7 envelope/payload schemas under `contracts/events/` are
untouched.

Choices that are expensive to reverse once producers and consumers exist: the
topic topology and message keying (ordering guarantees live here), the outbox
row shape (what the relay can ever know), delivery semantics (consumer
obligations), and how the relay is wired and run.

Alternatives considered:

- **Per-domain topics** (`workpulse.attendance`, `workpulse.employees`, …).
  Provisioning and versioning cost per topic with no ordering benefit: the
  domain's consistency need is per-employee, not per-domain.
- **Rebuild the envelope at publish time** from columns. Re-stamping
  `event_id`/`occurred_at` breaks the idempotency key and lies about domain
  time; a stored envelope is also the replayable record of what went on the bus.
- **Exactly-once / Kafka transactions.** Out of scope for v1; the product rule
  is replay-safe consumers (idempotent on `event_id`), which at-least-once plus
  dedupe delivers.
- **Separate `workers/relay` project with a uv workspace.** Cleaner isolation,
  but `apps/api` is `package=false` and no workspace exists (finding F4);
  deferred until a second worker arrives (see Decision 10).

## Decision

1. **One topic: `workpulse.events`.** Contract versioning lives in the
   envelope's `version` field (matching the `.v<N>` schema suffix), never in
   the topic name. Consumers filter on `event_type`. v1 runs with 1 partition /
   replication factor 1.
2. **Message key = UTF-8 `data.employee_id`** (all 7 payloads carry it). This
   is the per-employee ordering pin: one employee's transitions land on one
   partition and the relay's insertion-order publish gives per-partition order
   = commit order. Run-level events use `data.run_id` when no `employee_id`
   exists — see the 2026-10-21 amendment below.
3. **Value = the stored envelope as UTF-8 JSON**, published verbatim — the
   relay never rebuilds or re-stamps an envelope (`event_id` and `occurred_at`
   are the domain builder's, carrying the injected clock). "Verbatim" is
   semantic, not byte-wise: PostgreSQL `jsonb` may normalize object key order,
   so the published bytes are `json.dumps(stored_payload)` with possibly
   reordered keys; `event_id`, `occurred_at` and `data` are identical.
4. **No headers in v1.** Everything a consumer needs is in the envelope.
5. **At-least-once delivery.** The relay marks `published_at` only after the
   broker acks; a crash in between re-publishes the same `event_id`. Consumers
   **MUST** deduplicate on `event_id` and ignore unknown fields (already
   required by `.ai/conventions.md` → Events). No exactly-once claims.
6. **Outbox row shape `event_outbox`** (Alembic migration `0005`) — the relay's
   entire world:

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
   | `last_error` | `text` NULL | infrastructure error text only — never payload contents |

   Plus a partial index over unpublished rows (`WHERE published_at IS NULL`)
   for the relay's poll. Write-time validation is the pydantic `EventEnvelope`
   port model (structure + required `data.employee_id` — the message-key
   source); full Draft-2020-12 conformance of persisted payloads is proven in
   tests against `contracts/events/`, since `jsonschema` is a dev dependency
   and the port rejects junk loudly at write time.
7. **`attempts` counts every publish attempt, successes included** —
   incremented in the same `UPDATE` as `published_at`/`last_error`. It is a
   bookkeeping counter, not a failure count.
8. **Published rows are kept** — the append-only record of what went on the
   bus, mirroring `audit_entries`. Pruning/retention is a separate ops decision
   (Phase 6 at the earliest).
9. **Topic provisioning: the relay ensures `workpulse.events` at startup**
   (admin create, ignore-exists; 1 partition / rf 1 in v1) with broker
   auto-create as backstop. Growing partitions later preserves the keying pin
   of Decision 2.
10. **Relay wiring: `workers/relay/` runs inside the `apps/api` uv
    environment** — `aiokafka>=0.12` joins `apps/api/pyproject.toml`
    (ADR 0006's pre-agreed Phase-3 gate, justified by this spec per
    architecture rule 10), `pythonpath = [".", ".."]` for test imports, and the
    Makefile's `dev-relay` runs `python -m workers.relay.main` with
    `PYTHONPATH="..:."`. Cheapest honest wiring given no uv workspace; revisit
    as its own ADR when a second worker arrives.
11. **Poll order is `ORDER BY id` on the UUIDv7 PK.** `uuid_utils.uuid7()` is
    monotonic within a millisecond (verified: docstring + strictly increasing
    2000-id probe), so same-transaction appends (a late check-in's
    `attendance.checked_in` → `attendance.late` pair) keep builder order.
    Caveat: monotonicity is per-process — two API processes may interleave
    same-millisecond ids across transactions, which matches the row table's
    "≈ insertion order" hedge and is acceptable because per-employee writes are
    serialized per employee. v1 runs exactly one relay instance; multi-instance
    claiming is out of scope.

## Consequences

- Consumers (the ADR 0011 realtime swap, Leave, Notifications, Analytics) can
  be built against a fixed wire format: one topic, `data.employee_id` key,
  envelope value, dedupe on `event_id`, tolerate unknown fields and duplicates.
- The at-least-once crash window (broker acked, row unmarked) is a documented
  duplicate of the identical `event_id`, not a bug — harmless to any
  replay-safe consumer and the proof is an acceptance criterion
  (outbox-relay AC10).
- Strict publish order plus stop-on-first-failure means a permanently poisoned
  row head-of-line-blocks its partition (accepted: "retries, never drops", no
  DLQ in v1). It is the known failure mode of this design and belongs in
  Phase-6 observability.
- `last_error` carries infrastructure error text only. Payload contents — and
  therefore PII — never reach the error column (`.ai/security.md`).
- The write path never depends on the bus: a broker outage grows the backlog in
  PostgreSQL while user requests commit normally, and the relay drains in order
  once the broker returns (proven by outbox-relay AC9/AC12).
- `aiokafka` now lives in `apps/api` dependencies even though only the relay
  imports it (Decision 10); the alternative — a uv workspace or a separate
  worker project — is the documented upgrade when worker count grows.
- UUIDv7 ordering (Decision 11) is good enough for v1's single-process
  consistency needs; if multi-process same-millisecond interleaving ever
  matters, an explicit per-aggregate sequence column is the upgrade — a
  migration, not a redesign.
- The realtime Command Center keeps its Phase-2 best-effort Redis publish until
  the Kafka-fed consumer (ADR 0011's promise) replaces it as its own follow-up
  (outbox-relay "Out of scope").

## Amendment — 2026-10-21: run-level event keying

Payroll introduces the first **run-level** event, `payroll.calculated`
(`.ai/features/payroll.md` → Events), whose data carries `run_id`, `period` and
`employee_count` but no `employee_id`. The spec's Events table deliberately does
not emit a calculate event per employee (that would invent facts and explode the
event count), so Decision 2 is extended rather than replaced:

- **Message key = `data.employee_id` when present, otherwise `data.run_id`.**
  Employee-scoped events keep their per-employee ordering pin unchanged. A
  run-level event orders by run, which is the aggregate it describes.
- **The write gate accepts either field.** `EventEnvelope` (the outbox port's
  write-time model) requires at least one of `data.employee_id` or
  `data.run_id`; a payload with neither is still rejected as a junk envelope.
  The relay's publisher resolves the key with the same rule, so the writer and
  reader cannot disagree.
- **No version bump and no envelope change.** `event-envelope.v1.json` is
  untouched; the key is transport metadata derived from `data`, not a schema
  field. Consumers that only read `event_type`/`data` are unaffected.
- `payslip.issued` remains employee-keyed; it carries `employee_id` and is
  therefore unaffected by this amendment.
