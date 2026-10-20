# Feature: Notifications

- **Status:** approved
- **Phase:** 3
- **Size:** M
- **Surfaces:** api+events+workers (web surface untouched — no UI; delivery is worker-side)
- **Links:** `.ai/domain.md` (Notifications context · event catalog) · `leave.md` (trigger events) · `realtime-consumer.md` + ADR 0013 (consumer semantics precedent) · ADR 0012 (outbox transport, at-least-once + `event_id` dedupe) · `identity-access.md` (decision 4 — email-dependent identity flows deferred to this feature) · `.ai/security.md` (PII, logging) · `tasks/doc.md` §2 (multi-consumer fan-out)

## Problem

A leave decision or a late check-in reaches the affected people only if they
happen to be watching the wall or refreshing their requests page. The product's
promise is the opposite: one fact fans out to every consumer, and the
architecture names the notification worker as its third leg
(`.ai/architecture.md`: `Kafka ──► Notification worker ──► Email/Slack`). Without
it, approvals still live in whatever channel people happen to use outside the
system, and "who is late" is a manual sweep. This feature builds that leg:
domain events become person-addressed email and Slack messages, delivered
at-least-once, with every intent and delivery attempt persisted as
`Notification`/`Delivery` rows (`.ai/domain.md` aggregates).

## Business rules

- **Pure consumer.** The worker's only input is `workpulse.events` (ADR 0012
  topic). `Notification`/`NotificationDelivery` rows are a projection of the
  event stream — replay-safe, deduplicated on `event_id` — and nothing in the
  write path changes. Recipient and content enrichment (display name,
  `employee_facts.manager_id`, linked user email) is a **read-only reference
  lookup through a port**, never a projection input and never another context's
  tables directly (`.ai/domain.md` seam — events carry no PII, so addresses
  cannot come from the stream).
- **Trigger map (v1)** — exactly these five kinds:

  | `event_type` | Recipient | `kind` | Content fields (beyond own name) |
  | --- | --- | --- | --- |
  | `leave.requested` | the employee's team manager (`manager_id`) | `leave_requested` | employee name · leave type · inclusive dates · `days` |
  | `leave.approved` | the requester (`employee_id`) | `leave_approved` | leave type · dates · `days` · decider name |
  | `leave.rejected` | the requester | `leave_rejected` | leave type · dates · decider name |
  | `leave.withdrawn` | the requester (receipt of their own action) | `leave_withdrawn` | dates |
  | `attendance.late` | the late employee's team manager (`manager_id`) | `attendance_late` | employee name · `workday` · `minutes_late` |

  "Team manager" is the direct manager on `employee_facts.manager_id` — the
  same source leave's decide scope uses. Every other event type on the topic is
  ignored.
- **One notification per `(event_id, recipient)`** (unique). A redelivered
  envelope creates nothing new and never re-sends a delivery already `sent`.
- **Channels are independent deliveries of the same notification.** Email →
  the recipient's account address via **Resend**; Slack → the tenant's
  configured channel via the **Slack Web API** (`chat.postMessage`), the
  message naming the affected person. Every trigger is delivered on both
  channels in v1 (per-kind routing and preferences are out of scope).
- **Delivery state machine** (one row per `(notification_id, channel)`):

  ```text
  pending ─► sent                        (provider accepted)
          ├─► skipped (reason)           (channel not_configured / no address)
          └─► failed                     (provider error after ≤ 3 attempts)
  ```

  Transient provider errors retry in-process with bounded backoff (≤ 3
  attempts), then `failed` with `last_error` holding **infrastructure error
  text only** — never payload or message contents (ADR 0012 row-shape rule).
  A redelivered Kafka message re-attempts only deliveries not yet `sent`.
- **Consumer semantics** follow ADR 0013's shape. Stable group
  `workpulse-notifications` (`settings.notifications_consumer_group`), manual
  offset commit **after** the message's deliveries reach a terminal state —
  at-least-once toward the store (dedupe makes redelivery harmless), never a
  duplicate email for an already-`sent` delivery. `auto_offset_reset="latest"`:
  a first start begins at the topic tip — never a history replay into stale
  notifications. Malformed envelopes and provider outages never kill the loop
  (ids-only log, offset advances for unprocessable messages).
- **Content is server-side templates** (English in v1), rendered into a stored
  `subject` + `body` — the message of record. Templates may use exactly the
  content fields in the trigger table (display names, dates, `days`,
  `minutes_late`, leave type, decider name). They must **never** include
  `reason`, `decision_note` (`.ai/security.md`: sensitive), salaries, addresses,
  or any other free text from the database.
- **No PII in logs** — ids + outcome only (`.ai/security.md`). This includes
  degraded/unconfigured paths.
- **The worker is a trusted system process** (documented cross-tenant reader,
  alongside the audit writer, relay and realtime consumer): no user identity,
  reads every tenant's messages and reference data read-only; every row carries
  the envelope's `tenant_id`.
- **No audit entries.** Deliveries are not business mutations; the underlying
  transitions are already audited. The notification/delivery rows are the
  record of what was sent, to whom, and how it went.
- Tenant isolation (invariant 1) holds as everywhere else: repositories scope
  every query and insert by `tenant_id`.

## Scope

- New `notifications` domain: `Notification` + `NotificationDelivery`
  aggregates, tenant-scoped repository, recipient-directory + channel-sender
  ports, next Alembic migration (`0008_*`).
- Provider adapters for **Resend** (email) and **Slack Web API** (Slack).
  Dependencies per AGENTS.md rule 10: the official `resend` and `slack_sdk`
  packages, chosen over hand-rolled HTTP for auth, typed payloads and
  provider-side retry guidance (PO decision 4/6/7); both sync SDKs run through
  `anyio.to_thread` per `.ai/conventions.md`.
- New `workers/notifications/` consumer (mapping, templates, retry bookkeeping)
  wired like `workers/realtime/` (ADR 0013 decision 5: `apps/api` uv
  environment, `python -m workers.notifications.main`, `make dev-notifications`).
- Settings + `.env.example`: Resend API key + from-address, Slack bot token +
  channel id, consumer group.
- Gate: `check-api` real-broker proofs against `kafka-test` with deterministic
  fake sender adapters (providers are never hit in tests — `.ai/testing.md`);
  e2e stays regression-only. Worker lifecycle in the Makefile/script layer with
  deterministic log-line readiness (ADR 0013 decision 4), started for
  `dev-notifications` — **not** added to the `check-e2e` hop (no UI depends on
  it).
- ADR 0015 recording the expensive-to-reverse pins — delivery semantics
  (statuses, retries, offsets), trigger map, provider/SDK choice,
  `Notification`/`Delivery` model — **written before the worker code**
  (workflow rule 4).
- One clarifying line in `.ai/domain.md`'s pure-consumer rule (addressing seam
  above) in the same change.

## Out of scope

- **In-app notification inbox** (list, read/unread) and **per-user preferences /
  muting** — a separate spec if wanted; rows here are the durable substrate for
  one.
- Push, or any channel beyond email + Slack; per-kind channel routing.
- Email-based password reset and user-invite emails
  (`identity-access.md` decision 4 defers them to this capability; the flows
  themselves stay identity features).
- New event contracts — `notification.*` events are not added to the
  `.ai/domain.md` catalog in v1.
- Dead-letter/poison admin surfaces, replay tooling, delivery metrics dashboards
  (Phase 6), outbox-style ops HTTP for notifications.
- Template management, localization (templates live in code).
- Per-tenant provider credentials in the database or a tenant→channel map — v1
  is one env-configured Resend identity and one env-configured Slack channel
  (single dev tenant shape).
- Digesting/batching/quiet hours — every event is one message.
- Web/UI of any kind; no routes are added, `make gen-api` stays diff-free.
- Workflow consumers (`attendance.correction_requested`, overtime) — their own
  features.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/notifications/` (models · schemas · service · repository · ports · exceptions; no `policies.py`/`events.py` — no user-facing capability, no emitted events) |
| Persistence | `apps/api/app/infrastructure/postgres/` (`models_notifications.py` + notification repository) · Alembic `0008_*` |
| Provider adapters | `apps/api/app/infrastructure/notifications/` (Resend email · Slack Web API — exact placement is the plan's call) |
| Recipient directory | read-only port over identity/employees (display name · `manager_id` · linked user email) — implemented outside the domain, never importing another domain's internals |
| Events | `contracts/events/` — consumed only: `leave/*` v1 + `attendance/attendance-late.v1.json`; no contract changes |
| Workers | `workers/notifications/` (`main.py` + consumer mapping + templates, mirroring `workers/realtime/`) |
| Worker settings | `apps/api/app/config.py` (provider + channel + consumer-group knobs) · `.env.example` · `Makefile` (`dev-notifications`) |
| Web feature / routes | — (none) |
| API tests | `apps/api/tests/unit` (mapping, templates, retry/dedupe bookkeeping) · `tests/integration` (repository tenancy + unique key, service flow with fake senders) · `tests/contract` (consumed types vs `contracts/events/`) |
| E2E | `tests/e2e/` — regression only (must pass with the notification worker stopped) |

## Roles & access

- No routes and no UI — no role matrix rows change; `make gen-api` is
  diff-free. Denied-role testing is not applicable to a surface with no
  user-facing capability; the existing denied-role tests on check-in and leave
  decisions remain the guard for the mutations that trigger notifications.
- The worker is a trusted system process with no user identity (see Business
  rules). A denied mutation emits no event, therefore writes no notification
  rows — the notification store inherits the access control of its triggers.

## API contract

None added or changed. No route, request or response shape moves.

## Events

Consumed from `workpulse.events` (envelope per
`contracts/events/common/event-envelope.v1.json`, published verbatim by the
relay — ADR 0012). Consumer obligations: filter on `event_type`, deduplicate on
`event_id`, ignore unknown fields. The five trigger types and their payloads are
frozen by their existing v1 contracts (`leave.requested` / `approved` /
`rejected` / `withdrawn`, `attendance.late`); nothing in `contracts/events/`
changes, and the notification worker **emits no events**.

## Edge cases

- Employee has no `manager_id` (`leave.requested`, `attendance.late`) → no
  notification rows, ids-only log, offset advances.
- Recipient has no linked user account (non-employee manager, orphaned
  `manager_id`, terminated link) → same: no rows, ids-only log. A notification
  is person-addressed; nothing is sent "to the channel anyway".
- Resend API key unset (dev) → email delivery `skipped` (`not_configured`);
  Slack unaffected. Slack token/channel unset → slack delivery `skipped`
  (`not_configured`); email unaffected. Neither case fails the message.
- Provider timeout/error → ≤ 3 attempts with backoff → `failed`,
  payload-free `last_error`; the other channel's delivery still completes.
- Crash between provider accept and offset commit → redelivery: notification
  deduped, `sent` deliveries untouched, non-terminal deliveries re-attempted —
  never a duplicate email.
- Two worker instances racing the same message → the unique `(event_id,
  recipient)` constraint lets exactly one notification win; the loser is a
  dedupe no-op (v1 still runs one instance, like the relay).
- Malformed envelope or unknown `event_type` → ids-only log, offset advances,
  loop keeps running (bad-message resilience, ADR 0013's shape).
- `leave.withdrawn` notifies the requester as a receipt of their own action (PO
  decision 2) — not the manager in v1.
- A tenant's Slack channel is shared: messages name the affected person but
  never carry `reason`/`decision_note` or other sensitive text.
- First worker start with a long-running topic → nothing is sent: `latest`
  means the wall's "what changed now" reading, applied to notifications.

## Acceptance criteria

- [ ] Workers: given a `leave.requested` envelope for an employee whose manager has a linked user account, when the worker handles it, then one `leave_requested` notification is stored for the manager with an `email` delivery to their account address and a `slack` delivery to the tenant channel, both `sent` (fake adapters).
- [ ] Workers: given `leave.approved` / `leave.rejected` / `leave.withdrawn`, then the recipient is the requester's linked user; given `attendance.late`, then the recipient is the late employee's manager (`attendance_late`) — and no other event type produces rows.
- [ ] Workers (idempotence): given the same envelope handled twice (redelivery), then no second notification row exists and an already-`sent` delivery is not re-sent.
- [ ] Workers (failure): given the email adapter raising a provider error, when the message is handled, then that delivery is retried at most 3 times, ends `failed` with a payload-free `last_error`, the Slack delivery still completes, and the offset advances.
- [ ] Workers (failure): given an unresolvable recipient (no `manager_id` or no linked user account), then no rows are written, an ids-only log line is emitted, and the offset advances.
- [ ] Workers (failure): given a malformed envelope or an unknown `event_type`, then it is skipped with an ids-only log and the worker keeps consuming.
- [ ] Workers (config): given no Resend key / no Slack configuration, then the corresponding delivery is `skipped` (`not_configured`) and the other channel is unaffected.
- [ ] Workers (tenant isolation): given an event for tenant A, then every row carries tenant A's `tenant_id` and the Slack post targets tenant A's configured channel only.
- [ ] Content/PII: rendered subjects and bodies use only the trigger table's content fields — a test proves no `reason`, `decision_note` or other sensitive text reaches a message body, a log line or a `last_error`.
- [ ] Contracts: the worker consumes exactly the five trigger types, validated against the existing `contracts/events/` v1 schemas; `contracts/events/` is unchanged and `make gen-api` produces no diff.
- [ ] API (integration): the notification repository is tenant-scoped (tenant B sees none of tenant A's rows) and the unique `(event_id, recipient)` constraint refuses a duplicate insert.
- [ ] Unit: mapping and template rendering run without I/O, and retry/status bookkeeping is tested without wall-clock sleeps.
- [ ] E2E: the existing suite stays green with the notification worker stopped (regression only; delivery is proven at the worker level).

## Decisions (PO, 2026-10-20)

Scope answers (feature kickoff):

1. **Channels: email + Slack.** Push stays future. Rationale: matches the
   architecture diagram and the backlog's "email/Slack delivery".
2. **Trigger set: leave lifecycle + lateness** — `leave.requested` → team
   manager; `leave.approved`/`rejected`/`withdrawn` → requester;
   `attendance.late` → team manager. Closes the approval-workflow pain the
   product names with events that exist today; `attendance.correction_requested`
   and `employee.*` wait for their own consumers' needs. `leave.withdrawn` →
   the requester as a receipt of their own action (confirmation emails are
   normal); manager-side withdrawal notice is a possible follow-up.
3. **No web surface** — workers only. `Notification`/`Delivery` rows exist for
   idempotency, delivery status and future use; an inbox/preferences UI is its
   own spec.
4. **Provider SDKs, not SMTP/webhooks/raw HTTP** — official SDKs with API-key
   auth (justified per AGENTS.md rule 10 in Scope).
5. **Identity's email flows out of scope** — this feature is the delivery
   capability; password reset/invite flows are identity features consuming it
   later (`identity-access.md` decision 4).
6. **Email provider: Resend** (official `resend` Python SDK).
7. **Slack: per-tenant channel** (bot token + channel id, `chat.postMessage`)
   with the affected person named in the message; no Slack user-id mapping or
   DMs — that is identity data + admin surface this size does not need.

Implementation defaults (not product scope; follow ADR 0012/0013 precedent and
are cheap to revisit at `/plan`):

8. Delivery statuses, ≤ 3 in-process retries with backoff, payload-free
   `last_error` (ADR 0012's row discipline).
9. Consumer group `workpulse-notifications`, `auto_offset_reset="latest"`,
   manual offset commit after terminal delivery state (ADR 0013's shape, with
   commit-after-terminal instead of at-most-once: unlike the wall,
   notifications have no snapshot refresh to heal missed messages).
10. Both channels fire for every trigger in v1; per-kind routing lives with
    preferences (out of scope).
11. Message bodies are stored on the notification row (message of record;
    enables a later inbox and dev inspection without provider credentials).

## Plan

No API contract is pinned first (workflow rule 4): this feature adds no routes
and consumes only pre-existing, frozen `contracts/events/` v1 schemas — the
consumed contracts already stand "before the consumer", and T6 proves the
worker validates against them. `contracts/events/` is never edited.

### Acceptance criteria → changes (restated)

| # | Criterion (restated) | Satisfied by |
| --- | --- | --- |
| 1 | `leave.requested` (manager has linked account) → one `leave_requested` for the manager + `email` + `slack` deliveries, both `sent` (fakes) | T5 mapping/templates; T2 delivery service; T6 real-broker proof |
| 2 | `leave.approved`/`rejected`/`withdrawn` → requester; `attendance.late` → manager (`attendance_late`); no other type produces rows | T5 `map_envelope` trigger table; T6 contract test (only-five sweep) |
| 3 | Redelivery → no second notification; a `sent` delivery is not re-sent | T3 unique `(event_id, recipient_employee_id)` + `get_or_create`; T2 pending-only re-attempt; T6 redelivery proof |
| 4 | Email adapter error → ≤ 3 attempts, `failed` with payload-free `last_error`, Slack still completes, offset advances | T2 retry bookkeeping (injected sleep); T2/T4 safe-error capture; T6 proof |
| 5 | Unresolvable recipient (no `manager_id` / no linked account) → no rows, ids-only log, offset advances | T5 consumer skip path; T6 proof |
| 6 | Malformed envelope / unknown `event_type` → skipped, ids-only log, loop keeps consuming | T5 consumer `_parse`/mapping; T6 proof |
| 7 | No Resend key / no Slack config → that delivery `skipped` (`not_configured`), other channel unaffected | T4 adapters raise `NotConfiguredError`; T2 maps to `skipped`; T6 proof |
| 8 | Tenant isolation: all rows carry the event's `tenant_id`; Slack targets the configured channel only | T3 tenant-scoped repository; T2/T6 assert channel id + row tenants |
| 9 | Content/PII: bodies use only trigger-table fields; no `reason`/`decision_note`/sensitive text in body, log line or `last_error` | T5 templates (pure allowlist); T2 safe `last_error`; T4/T6 hostile-payload proofs |
| 10 | Consumes exactly the five trigger types vs `contracts/events/`; contracts unchanged; `make gen-api` diff-free | T6 contract test; T6 gate sweep (nothing touches routes/contracts) |
| 11 | Repository tenant-scoped (B sees none of A's rows); unique `(event_id, recipient)` refuses duplicate insert | T3 integration tests |
| 12 | Mapping + templates I/O-free; retry/status bookkeeping without wall-clock sleeps | T2/T5 unit tests (pure functions, recording sleeper) |
| 13 | E2E stays green with the notification worker stopped (regression only) | T6: `check-e2e`/`full-hop.sh` untouched, worker never joins the hop |

### Repo research findings the plan relies on

- **F1 — Worker shape.** `workers/realtime/` is the template: `aiokafka`
  `AIOKafkaConsumer(topic, group_id=..., auto_offset_reset="latest",
  enable_auto_commit=False)`, structural `KafkaMessage`/`EventSource` Protocol
  ports inside the worker module, per-message `_handle` → `_commit`, ids-only
  warnings (`consumer_message_skipped`, `consumer_fetch_failed`,
  `consumer_commit_failed`), exponential fetch backoff, `started`
  `asyncio.Event` readiness + `realtime_consumer_started`/`_stopped` log lines.
  `workers/relay/` adds the `SessionFactory` structural port and `_safe_error`
  redaction (payload-string scrub, 300-char cap) for row errors.
- **F2 — Settings.** `pydantic-settings`, no `env_prefix`; naming precedent
  `realtime_consumer_group`, `realtime_poll_seconds`, `realtime_backoff_*`.
  `.env.example` does **not** exist yet (outbox-relay F3/R7 deferred it; this
  spec's Scope names it).
- **F3 — Persistence.** Domain tables live in `app/domains/<d>/models.py`;
  cross-cutting projection tables live in
  `app/infrastructure/postgres/models_<thing>.py` (`models_outbox.py`,
  `models_audit.py`) → notifications tables follow `models_notifications.py`.
  Migrations are append-only; latest is `0007_leave_coverage_index`, so
  `0008_*` is free. `alembic/env.py` registers tables via side-effect imports.
  Partial/covering indexes must be declared in both migration and
  `__table_args__` (parity rule) — this plan uses plain indexes only.
- **F4 — Seam precedent.** Infrastructure implements domain ports and may read
  other domains' ORM: `PostgresEmployeeLink` (identity's port over
  `employees`); leave's `EmployeeFacts` reads `employees.manager_id`. The
  manager source is `employees.manager_id` (migration `0002_employees`),
  matching leave's decide scope.
- **F5 — Identity facts.** `users` (email = account address, `employee_id`
  optional link, no FK; global `uq_users_email`) links people to accounts;
  `employee_link_taken` keeps at most one linked account per employee at the
  service level. `decided_by` is an actor **user** id (ADR 0010), not an
  employee id. Display names are `employees.first_name`/`last_name`.
- **F6 — Contracts frozen.** Five trigger schemas
  (`contracts/events/leave/leave-{requested,approved,rejected,withdrawn}.v1.json`,
  `contracts/events/attendance/attendance-late.v1.json`) + envelope v1
  (`additionalProperties: false`). Field pins: `approved` has `days` +
  `decided_by`; `rejected` has `decided_by`, no `days`; `withdrawn` has
  neither; `attendance.late` has `workday`, `shift_id`, `minutes_late`.
  Contract tests use `jsonschema` `Draft202012Validator` loaded by path.
- **F7 — Test wiring.** `tests/conftest.py` pins test DB :5433 / Redis :6380 /
  Kafka :9093 before any `app.*` import; integration fixture migrates +
  truncates per test; fake-port precedent is
  `tests/unit/outbox/fakes.py::RecordingEventOutbox`. No pytest markers exist —
  layering is the `tests/{unit,integration,contract}` directories. `pythonpath`
  covers `../..`, so `workers.*` imports in tests work.
- **F8 — Gates.** `check-api` already runs `kafka-test-up` (real broker :9093)
  and lints/types `../../workers`; `check-e2e` runs the full hop
  (`full-hop.sh`: relay + realtime) and must not gain the notification worker.
  Makefile dev-target recipe:
  `cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.<w>.main`.
- **F9 — PII discipline.** `.ai/security.md`: never log PII — ids + outcome
  only; error messages carry stable codes, never field values; events carry no
  PII. Trusted cross-tenant process wording lives in feature specs'
  Roles-&-access sections (outbox-relay precedent), not in `security.md`.
- **F10 — Deps.** `resend` and `slack-sdk` are absent; workers run in the
  `apps/api` uv env, so both land in `apps/api/pyproject.toml` (spec PO 4/6/7
  is the AGENTS.md rule-10 justification). Sync SDKs go through
  `anyio.to_thread.run_sync` (identity argon2 precedent).

### Ordered tasks

**T1 — ADR 0015 + `.ai/domain.md` seam line (docs, before any worker code).**
- New `.ai/decisions/0015-notifications-delivery-and-trigger-map.md` pinning:
  the `Notification`/`NotificationDelivery` model and unique key — `recipient`
  is the **employee UUID** (the person) as in the trigger table; delivery state
  machine (`pending → sent | skipped(reason) | failed`, ≤ 3 attempts, bounded
  backoff, `last_error` per ADR 0012's `_safe_error` discipline); offset
  semantics — group `workpulse-notifications`, `latest`, commit only after the
  message's deliveries are terminal, strict order (never commit past a message
  the store refused), at-least-once toward the store; the five-kind trigger
  map; provider/SDK choice (Resend + `slack_sdk`, `anyio.to_thread`); stored
  `subject`/`body` as message of record (PO decision 11) with the PII stance
  (names allowed in bodies only; `reason`/`decision_note` never; logs and
  `last_error` PII-free); Resend idempotency key + the residual Slack
  duplicate-post window (R2); trusted-system-process stance.
- `.ai/domain.md` — one clarifying line in the Notifications pure-consumer
  rule: recipient/content enrichment is a read-only reference lookup through a
  port (events carry no PII), never a projection input and never another
  context's tables.
*Tests:* none (docs-only).
*Verify:* diff touches only `.ai/`; `make check-api` unaffected.

**T2 — Notifications domain core (`apps/api/app/domains/notifications/`, pure).**
- `models.py` — `NotificationKind`, `DeliveryChannel`, `DeliveryStatus`
  StrEnums; frozen `Notification` / `NotificationDelivery` domain dataclasses;
  `RenderedMessage` (subject/body).
- `schemas.py` — the worker↔domain seam DTOs: `NotificationIntent` (kind,
  tenant_id, event_id, event_type, recipient employee id, content-field bag),
  `SubjectFacts` (display name, `manager_id`), `Recipient` (employee id, user
  id, display name, account address).
- `ports.py` — `RecipientDirectory` (subject facts · recipient account · actor
  display name — all read-only), `EmailSender`, `SlackSender` (send raises;
  `NotConfiguredError` marks the unconfigured case).
- `exceptions.py` — `NotificationError(WorkPulseError)` family +
  `NotConfiguredError` (port-level).
- `repository.py` — `NotificationRepository(Protocol)`, tenant_id on every
  method (leave precedent): `get_or_create` (returns created flag), delivery
  add + status transitions, list deliveries by notification.
- `service.py` — `NotificationService.handle(...)`: dedupe via tolerant
  `get_or_create`; create `email` + `slack` delivery rows; drive each
  non-terminal delivery — `NotConfiguredError` → `skipped(not_configured)`
  (no retry), any other provider exception → ≤ 3 attempts with injected
  `sleep` (doubling backoff), then `failed` with safe `last_error`
  (`type(exc).__name__` + `_safe_error`-style redacted, 300-capped text;
  rendered subject/body substrings scrubbed); channels are independent; only
  `pending` deliveries are re-attempted on redelivery; returns a terminal
  summary for the commit decision. Store errors propagate (caller decides).
*Tests:* `apps/api/tests/unit/notifications/{fakes.py,test_delivery_service.py}`
(recording fakes mirroring `RecordingEventOutbox`; recording sleeper):
retry bookkeeping with no wall-clock sleeps; ≤ 3 attempts then `failed`;
hostile provider error echoing the body never survives into `last_error`;
`not_configured` skip without touching the other channel; redelivery
re-attempts only `pending`; duplicate handle is a no-op.
*Verify:* `make check-api` (pure units, no DB).

**T3 — Persistence + migration + directory adapter.**
- `apps/api/app/infrastructure/postgres/models_notifications.py` —
  `notifications`: `id` (uuid7), `tenant_id`, `event_id`, `event_type`, `kind`,
  `recipient_employee_id`, `recipient_user_id` (account snapshot), `subject`,
  `body`, `created_at`;
  `UniqueConstraint(event_id, recipient_employee_id)` +
  `ix_notifications_tenant_id`. `notification_deliveries`: `id`, `tenant_id`,
  `notification_id` (FK), `channel`, `status`, `attempts`, `last_error`,
  `sent_at`, timestamps; `UniqueConstraint(notification_id, channel)` +
  tenant index.
- `apps/api/app/infrastructure/postgres/repositories/notifications.py` —
  `PostgresNotificationRepository(session)`; every statement scoped
  `WHERE tenant_id = :tenant`; `get_or_create` is insert-on-conflict-do-nothing
  + fetch (or IntegrityError-tolerant) so a racing duplicate is a dedupe no-op.
- `apps/api/app/infrastructure/postgres/recipient_directory.py` —
  `PostgresRecipientDirectory(session)`, read-only over `employees` + `users`
  ORM (F4 precedent); account address is `users.email` (the account, not
  `employees.email`); display name is `first_name`/`last_name`; actor name
  resolves user id → linked employee; if >1 active link exists, deterministic
  pick + ids-only log (R6).
- `apps/api/alembic/versions/0008_notifications.py` — both tables +
  constraints/indexes; register the model module in `alembic/env.py`.
- `tests/integration/conftest.py` — service/repository fixtures mirroring the
  leave fixtures.
*Tests:* `tests/integration/test_notifications_repository.py` (tenant B sees
none of tenant A's rows; duplicate `(event_id, recipient)` refused by the DB;
`(notification_id, channel)` unique; status transitions persist),
`test_notifications_flow.py` (service over the real DB with fake senders),
`test_notifications_directory.py` (manager/no-manager; account/no-account).
*Verify:* `make check-api` (migration exercised by the integration fixture and
`test_migrations.py`).

**T4 — Settings, deps, provider adapters, `.env.example`.**
- `apps/api/app/config.py` — Phase-3 notifications block per F2 naming:
  `notifications_consumer_group="workpulse-notifications"`,
  `notifications_poll_seconds`, `notifications_backoff_base_seconds`,
  `notifications_backoff_max_seconds`, `notifications_send_max_attempts=3`,
  `notifications_send_backoff_base_seconds`, `resend_api_key`,
  `resend_from_address`, `slack_bot_token`, `slack_channel_id` (nullable →
  unconfigured).
- `apps/api/pyproject.toml` — add `resend`, `slack-sdk` (F10).
- `apps/api/app/infrastructure/notifications/` — `resend_email.py`
  (`ResendEmailSender`) and `slack_web.py` (`SlackWebSender`) implementing the
  domain ports: sync SDK calls via `anyio.to_thread.run_sync`; missing
  credentials raise `NotConfiguredError`; provider failures raise (service
  captures safe text); Resend send carries an idempotency key = delivery id
  (R2); Slack `chat.postMessage` targets `slack_channel_id` and names the
  affected person (content fields only).
- `.env.example` (repo root) — every `Settings` key documented (closes this
  Scope line and outbox-relay F3/R7).
*Tests:* `apps/api/tests/unit/notifications/test_senders.py` with fake SDK
clients (providers never hit — `.ai/testing.md`): configured send crosses the
thread bridge; unset key/token → `NotConfiguredError`; provider error → raises
with infra-safe text; Slack message names the person and contains no forbidden
fields.
*Verify:* `make check-api`.

**T5 — Notification worker (`workers/notifications/`) + `make dev-notifications`.**
- `workers/notifications/__init__.py` — layer docstring (mirrors
  `workers/realtime/`).
- `mapping.py` — pure `map_envelope(envelope) -> NotificationIntent | None`:
  exactly the five trigger kinds and recipients of the trigger table
  (`manager_id` for `leave.requested`/`attendance.late`; `employee_id` for
  `leave.approved`/`rejected`/`withdrawn`); known-but-untriggering types
  (attendance check-in/out, employee.*) → None silently; unknown `event_type`
  → None with ids-only warn; trigger type with invalid payload →
  `invalid_envelope` warn (realtime's `is_realtime_envelope` discrimination).
- `templates.py` — pure `render(...) -> RenderedMessage`: English v1, exactly
  the trigger-table content fields, ISO dates, `days`, `minutes_late`; the
  decider clause is omitted when the actor name does not resolve (R3); never
  `reason`, `decision_note`, salaries, addresses or free text.
- `consumer.py` — `NotificationsConsumer` mirroring `RealtimeConsumer`:
  structural `EventSource`/`KafkaMessage` ports, fetch → parse → map → resolve
  → render → `service.handle` → **commit only after the message's deliveries
  are terminal** (skips — malformed/unknown/unresolvable — commit too);
  store failure → bounded in-process backoff, never commit past the message,
  never kill the loop; `started` readiness event; ids-only logs;
  `stop()`/signal semantics as realtime.
- `main.py` — settings + logging; `AIOKafkaConsumer(settings.kafka_events_topic,
  group_id=settings.notifications_consumer_group,
  auto_offset_reset="latest", enable_auto_commit=False)`; session factory
  (relay shape); directory + repository + service + sender wiring; signal
  handlers; `notifications_consumer_started` / `_stopped` log lines (ADR 0013
  decision 4 shape).
- `Makefile` — `dev-notifications` target (recipe mirrors `dev-realtime`) +
  `.PHONY`.
*Tests:* `apps/api/tests/unit/workers/test_notifications_mapping.py` (five
kinds + recipient derivation + content pins incl. the days/decided_by asymmetry
from F6; other types → None), `test_notifications_templates.py` (content
allowlist; hostile fields cannot render), `test_notifications_consumer.py`
(stub source: malformed/unknown skip + commit + loop continues; unresolvable
recipient → no rows + ids-only + commit; commit-after-terminal; store failure →
no commit and loop alive; `started` sets on first fetch).
*Verify:* `make check-api` (ruff/pyright already cover `../../workers`).

**T6 — Real-broker proofs + contract tests + PII sweep + gate sweep.**
- `apps/api/tests/integration/test_notifications_consumer_kafka.py` — the
  AC-level proofs on `kafka-test` (:9093, already in `check-api`): real
  envelopes (leave/attendance event builders) produced **after**
  `consumer.started` (deterministic readiness, never a sleep), consumer run
  with `RecordingEmailSender`/`RecordingSlackSender` + real directory/repository
  on the test DB. Covers AC 1–8 end to end per message: happy paths per
  trigger; redelivery dedupe; email failure (≤ 3 attempts, `failed`,
  payload-free `last_error`, Slack completes, offset advances — next message
  still handled); unresolvable recipient; malformed/unknown; per-channel
  `not_configured`; `tenant_id` on every row + Slack target = configured
  channel. Bounded `asyncio.wait_for`, no bare sleeps.
- `apps/api/tests/contract/test_notifications_consumed_events.py` — mapping's
  consumed payloads validate against the five `contracts/events/` v1 schemas
  (`Draft202012Validator` by path, mirroring `test_leave_events.py`); sweep
  every `contracts/events/**/*.v1.json` type and assert only the five produce
  intents; pin the F6 field asymmetries.
- PII sweep (hostile payload): an envelope whose `data` carries
  `reason`/`decision_note` and a provider error echoing the rendered body —
  assert none of it reaches `subject`/`body`, captured structlog lines, or
  `last_error` (unit pieces already in T2/T4/T5; this is the end-to-end proof).
- Gate sweep: `make gen-api` diff-free (no routes moved);
  `tests/e2e/full-hop.sh` untouched and green with the worker stopped
  (regression only).
*Verify:* `make check`.

**T7 — Record.**
This spec: all AC boxes ticked, `Status: done`. `.ai/features/README.md`
backlog row → `done`; `tasks/TODO.md` row ticked. ADR 0015 linked from the
Decisions list above.
*Verify:* `make check`; docs diff consistent.

### Risks, unknowns, ADR-worthy decisions

- **R1 — ADR 0015 is the expensive-to-reverse record** (delivery semantics,
  trigger map, provider/SDK, model): written first in T1 per workflow rule 4.
- **R2 — Duplicate-send window.** A crash between provider accept and the
  `sent` mark leaves a `pending` row that redelivery re-attempts. Mitigations:
  mark `sent` immediately after each provider call (before the other channel,
  before the offset commit) and send Resend with an idempotency key
  (= delivery id) so the provider collapses the retry. Slack has no idempotency
  key — a crash window may double-post to the channel (accepted channel noise;
  email, the person-addressed channel, is protected).
- **R3 — Decider name.** `decided_by` is an actor user id (F5); non-employee
  deciders (admin/HR accounts) have no employee display name. Plan default:
  the decider clause renders only when a display name resolves, otherwise the
  sentence omits it. Cheap to revisit; call out at review.
- **R4 — `.env.example` does not exist** despite Docs/Safety conventions and
  this spec's Scope. T4 creates it at the repo root covering all `Settings`
  keys (also closes outbox-relay's F3/R7 gap). If review prefers scoping it
  out, drop the file from T4 — the settings themselves still land in
  `config.py`.
- **R5 — Real-broker test flakiness.** Deterministic shape: wait for
  `consumer.started` before producing, bounded `asyncio.wait_for` on row
  state, fake senders, per-test DB truncation and a unique consumer group per
  test run. `kafka-test` is already a `check-api` dependency.
- **R6 — Multiple user accounts per employee.** Service-forbidden
  (`employee_link_taken`) but not DB-constrained. The directory picks
  deterministically (lowest id) + ids-only log; no new constraint — identity
  owns that invariant.
- **R7 — Store outage semantics.** The consumer backoffs in-process and never
  commits past an unhandled message (relay's strict-order stance): at-least-once
  toward the store is preserved and dedupe makes redelivery harmless. A
  poison store error could stall a single-instance worker — acceptable in v1
  (no DLQ/ops surface in scope).
- **R8 — Stored bodies contain personal names.** PO decision 11 (message of
  record) deliberately stores rendered `subject`/`body`; this is the one place
  names persist. `reason`/`decision_note` never enter them; logs and
  `last_error` stay PII-free. Noted in ADR 0015.
- **R9 — `recipient` = employee UUID.** The unique key is
  `(event_id, recipient_employee_id)` per the trigger table's person-addressed
  recipients; `recipient_user_id` is the account snapshot. A future inbox that
  wants user-addressed rows would revisit this via ADR.

### Test strategy (gates × failure cases)

| Gate | What runs | New/changed tests | Failure cases covered |
| --- | --- | --- | --- |
| `check-api` (unit) | `tests/unit/notifications`, `tests/unit/workers` | delivery service + fakes; senders; mapping; templates; consumer loop | retry ≤ 3 + payload-free `last_error` (T2); `not_configured` per channel (T4); redelivery re-attempts only `pending` (T2); malformed/unknown skip + loop alive (T5); unresolvable recipient (T5); hostile `reason`/`decision_note` never rendered/logged (T2/T4/T5); no wall-clock sleeps (T2) |
| `check-api` (integration) | `tests/integration` | repository, flow, directory, real-broker consumer proofs | tenant B sees none of A's rows + duplicate `(event_id, recipient)` refused (T3); email provider error → `failed` + Slack completes + offset advances (T6); crash-redelivery dedupe (T6); no-config skips (T6); tenant `tenant_id` + Slack channel target (T6) |
| `check-api` (contract) | `tests/contract` | consumed types vs `contracts/events/` v1 | only the five trigger types produce intents; field pins (approved/rejected/withdrawn asymmetry); contracts unchanged (T6) |
| `check-e2e` | `tests/e2e` (unchanged) | none | regression: suite green with the notification worker stopped; hop never gains the worker |
| `make gen-api` | generated client | none | diff-free — no routes/request/response shapes moved |
