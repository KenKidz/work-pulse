# 15. Notification delivery and trigger map

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

`.ai/features/notifications.md` builds the third leg of the event backbone:
`workpulse.events` → notification worker → email/Slack (`.ai/architecture.md`).
The worker is a pure consumer; the write path, the five trigger event contracts
and `contracts/events/` do not change. The choices that are expensive to
reverse once rows exist and messages have been sent:

1. **What a notification is.** The `Notification`/`NotificationDelivery` row
   shape, the uniqueness key (what "already notified" means) and who the
   recipient is addressed by.
2. **Delivery semantics.** The delivery statuses, retry bound, what `last_error`
   may contain, and when a Kafka offset may advance — a wrong choice here
   duplicates or silently drops person-addressed messages.
3. **The trigger map.** Which event types notify whom, and with which content
   fields; adding kinds later is cheap, changing recipients or dedupe is not.
4. **Provider stack.** Resend for email and the Slack Web API for Slack, and
   whether adapters block the event loop.
5. **Message of record and PII.** Whether rendered content is stored, and where
   personal data may appear (bodies, logs, error columns).
6. **Process trust.** The worker has no user identity and must read every
   tenant's event stream and reference data — a documented trusted system
   process, like the audit writer, relay and realtime consumer.

Alternatives considered:

- **SMTP / raw HTTP / webhooks instead of provider SDKs.** Hand-rolled auth,
  retries and payloads for no benefit; the official `resend` and `slack_sdk`
  packages give typed payloads and provider-side retry guidance (spec PO
  decisions 4/6/7, AGENTS.md rule 10). Rejected.
- **Enriching recipients from the event stream.** Events deliberately carry no
  PII (`.ai/conventions.md`, `.ai/security.md`), so addresses and display names
  cannot come from the bus. A read-only recipient-directory port over
  identity/employees is the only seam that respects the context boundary.
  Rejected.
- **Commit-after-attempt, at-most-once toward the notification store** (the
  realtime consumer's shape, ADR 0013). The wall heals missed deltas via its
  ~60s snapshot refresh; there is no snapshot for notifications, so a crash
  after commit would silently lose a message. Rejected in favour of
  commit-after-terminal-delivery.
- **Kafka transactions / exactly-once.** Out of scope for v1; at-least-once
  plus dedupe on `event_id` gives the required "never a duplicate email"
  outcome (ADR 0012 decision 5).
- **Slack DMs or per-user Slack identity mapping.** Requires an identity
  surface and user-id mapping this feature does not need; a tenant channel
  message naming the affected person is the v1 shape (spec PO decision 7).
- **Render on read / no stored body.** Leaves the delivery un-auditable
  without provider credentials and blocks a later inbox; PO decision 11 makes
  the rendered message the record.

## Decision

1. **Model and addressing.** Two tenant-scoped tables (Alembic `0008_*`):

   `notifications` — the intent, one row per `(event_id, recipient)`:

   | Column | Type | Meaning |
   | --- | --- | --- |
   | `id` | `uuid` PK (UUIDv7) | row id |
   | `tenant_id` | `uuid` NOT NULL | envelope's tenant (indexed) |
   | `event_id` | `text` NOT NULL | envelope's `evt_…` — dedupe key |
   | `event_type` | `text` NOT NULL | one of the five trigger types |
   | `kind` | `text` NOT NULL | notification kind (`leave_requested`, …) |
   | `recipient_employee_id` | `uuid` NOT NULL | the person addressed (trigger table) |
   | `recipient_user_id` | `uuid` NOT NULL | account snapshot resolved at consume time |
   | `subject` | `text` NOT NULL | rendered subject (message of record) |
   | `body` | `text` NOT NULL | rendered body (message of record) |
   | `created_at` | `timestamptz` NOT NULL | write time |

   Unique `(event_id, recipient_employee_id)`; index on `tenant_id`.
   **The recipient is the employee UUID — the person — as in the trigger
   map;** `recipient_user_id` is the account snapshot used for the email
   address, not the identity of the notification. A redelivered envelope loses
   the unique-key race as a dedupe no-op. A future user-addressed inbox
   revisits the key via its own ADR (spec R9).

   `notification_deliveries` — one row per `(notification, channel)`:

   | Column | Type | Meaning |
   | --- | --- | --- |
   | `id` | `uuid` PK (UUIDv7) | row id |
   | `tenant_id` | `uuid` NOT NULL | copied from the notification (indexed) |
   | `notification_id` | `uuid` NOT NULL FK | parent notification |
   | `channel` | `text` NOT NULL | `email` \| `slack` |
   | `status` | `text` NOT NULL | `pending` \| `sent` \| `skipped` \| `failed` |
   | `attempts` | `int` NOT NULL DEFAULT 0 | send attempts, successes included |
   | `last_error` | `text` NULL | infrastructure error text only |
   | `sent_at` | `timestamptz` NULL | provider-accept time |
   | `created_at` / `updated_at` | `timestamptz` NOT NULL | bookkeeping |

   Unique `(notification_id, channel)`; index on `tenant_id`. Both channels
   are independent deliveries of the same notification — one may be `sent`
   while the other is `skipped` or `failed`.

2. **Delivery state machine.**

   ```text
   pending ─► sent                        (provider accepted; sent_at set)
           ├─► skipped (reason)           (not_configured / no_address; terminal)
           └─► failed                     (provider error after ≤ 3 attempts)
   ```

   Transient provider errors retry in-process with bounded exponential
   backoff, at most **3 attempts** total, through an injected sleeper so tests
   never wait on the wall clock. Only `pending` deliveries are re-attempted on
   redelivery; `sent`, `skipped` and `failed` are terminal and never re-sent.
   `NotConfiguredError` (missing Resend key/from-address, Slack token/channel)
   maps to `skipped(not_configured)` **without retry** and never fails the
   message; the other channel proceeds.

   `last_error` follows ADR 0012's row discipline: error type plus redacted
   infrastructure text, capped at 300 characters, with rendered subject/body
   substrings scrubbed. Payload contents, message bodies and PII never reach
   the column. Each channel is marked `sent` **immediately after its provider
   accepts**, before the other channel runs and before the offset commit.

3. **Offset semantics.** Stable consumer group
   `workpulse-notifications` (`settings.notifications_consumer_group`),
   `auto_offset_reset="latest"`, `enable_auto_commit=False`. Offsets commit
   **manually per message, only after that message's deliveries reach a
   terminal state** (or after a deliberate skip: malformed envelope, unknown
   `event_type`, unresolvable recipient). Order is strict per partition: never
   commit past a message the store refused — store errors back off in-process
   and leave the offset uncommitted. Net contract: **at-least-once toward the
   store** (dedupe on `event_id` makes redelivery harmless) and **never a
   duplicate email for an already-`sent` delivery**. A first start begins at
   the topic tip — never a history replay into stale notifications — while a
   restart resumes committed offsets. A poisoned store error can stall the
   single v1 worker (no DLQ in scope; spec R7).

4. **Trigger map (v1).** Exactly these five kinds; every other event type on
   the topic is ignored (known-but-untriggering types silently, unknown types
   with an ids-only warning). "Team manager" is `employees.manager_id`, the
   direct manager and the same source leave's decide scope uses.

   | `event_type` | Recipient | `kind` | Content fields beyond the recipient's name |
   | --- | --- | --- | --- |
   | `leave.requested` | the employee's team manager | `leave_requested` | employee name · leave type · inclusive dates · `days` |
   | `leave.approved` | the requester (`employee_id`) | `leave_approved` | leave type · dates · `days` · decider name |
   | `leave.rejected` | the requester | `leave_rejected` | leave type · dates · decider name |
   | `leave.withdrawn` | the requester (receipt of their own action) | `leave_withdrawn` | dates |
   | `attendance.late` | the late employee's team manager | `attendance_late` | employee name · `workday` · `minutes_late` |

   `decided_by` is an actor **user** id (ADR 0010); the decider clause renders
   only when that user resolves to an employee display name, otherwise the
   sentence omits it (spec R3). An employee with no `manager_id`, or a
   recipient with no linked user account, produces **no rows** — a
   notification is person-addressed and is never sent "to the channel anyway".
   The worker emits no events and writes no audit entries; the trigger
   mutation is already audited, and the notification/delivery rows are the
   record of what was sent.

5. **Provider SDKs.** Email uses the official `resend` Python SDK; Slack uses
   the official `slack_sdk` Web API (`chat.postMessage`) targeting the
   per-tenant configured channel, with the affected person named in the
   message. v1 is one env-configured Resend identity and one env-configured
   Slack channel (single dev-tenant shape); per-tenant provider credentials
   and a tenant→channel map are out of scope. Both SDKs are synchronous and
   run through `anyio.to_thread.run_sync` per `.ai/conventions.md`, so the
   consumer loop is never blocked. Providers are never called in tests
   (`.ai/testing.md`): gate proofs use deterministic fake sender adapters.

6. **Content is the stored message of record.** Server-side English templates
   render `subject` + `body` at consume time and persist them (PO decision 11),
   enabling a later inbox and dev inspection without provider credentials.
   Templates may use exactly the trigger map's content fields — display names,
   leave type, inclusive dates, `days`, `minutes_late`, decider name — and
   **never** `reason`, `decision_note`, salaries, addresses or any other free
   text. Personal names are therefore the one PII that intentionally persists
   in the body (spec R8). Logs and `last_error` stay PII-free: ids and outcome
   only, including degraded and unconfigured paths (`.ai/security.md`). Email
   goes to the recipient's account address (`users.email`), not
   `employees.email`.

7. **Duplicate-send window.** A crash between provider accept and the `sent`
   mark leaves a `pending` row that redelivery re-attempts. Email sends carry
   an idempotency key equal to the delivery id, so Resend collapses the retry;
   marking `sent` immediately after each accept narrows the window. Slack has
   no idempotency key: a crash inside that window may double-post to the
   channel — accepted channel noise in v1. The person-addressed email channel
   is protected; a redelivered Kafka message never re-sends an already-`sent`
   delivery.

8. **The worker is a trusted system process.** Documented alongside the audit
   writer, relay and realtime consumer: no user identity, consumes every
   tenant's messages and reads recipient/display-name reference data
   read-only through ports. It never imports another context's domain
   internals, never mutates their tables, and every row it writes carries the
   envelope's `tenant_id`; repositories scope every statement by `tenant_id`.

## Consequences

- The write path remains bus-free and the five trigger contracts plus
  `contracts/events/` are untouched; the worker consumes only frozen v1
  schemas and emits nothing.
- Redelivery is safe by construction: unique `(event_id,
  recipient_employee_id)` produces at most one notification, and terminal
  deliveries are never re-sent. The remaining duplicate is the documented
  Slack crash window, not an email duplicate.
- Commit-after-terminal means a store outage stops offset advancement rather
  than losing a notification (at-least-once toward the store); a permanently
  poisoned message can stall the single worker until an operator intervenes —
  the known v1 failure mode, with DLQ/ops surfaces deferred to Phase 6.
- `auto_offset_reset="latest"` deliberately drops pre-first-start history:
  notifications describe "what changed now", not a backfill.
- The recipient-directory port keeps the pure-consumer rule intact: the
  notification store is rebuildable from the event stream plus read-only
  reference lookups, never from another context's write path.
- Rendered names persist in `subject`/`body` by design; logs and `last_error`
  remain PII-free, so observability never becomes a second copy of HR data.
- One env-configured Resend identity and Slack channel are a dev-tenant
  simplification; per-tenant credentials and channel routing are a later
  feature, not a schema change to these rows.
- Identity's deferred email flows (password reset, invites;
  `identity-access.md` decision 4) can later reuse the delivery capability
  without changing this model.
