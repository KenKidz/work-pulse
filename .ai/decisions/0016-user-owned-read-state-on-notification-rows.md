# 16. User-owned read state on notification rows

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

`.ai/features/notification-inbox.md` adds the person-addressed inbox over the
notification rows ADR 0015 pins. Those rows are a pure projection of
`workpulse.events` — the worker writes them and a rebuild from the stream
recreates them (`.ai/domain.md`). Read state is different: it is not derived
from any event, it changes when a user looks at their inbox, and losing it in a
rebuild is acceptable. The choices that set a precedent:

1. **Where read state lives.** A column on `notifications` or a sidecar table.
2. **Who may write it.** The worker never sees it; the HTTP read path must be
   allowed to write exactly this one field without reopening the projection to
   other mutations.
3. **What a rebuild does to it.** Event-sourced projections are disposable;
   user state layered on them is not event-derived.
4. **Audit and events.** Whether marking a notification read is a sensitive
   mutation or a business fact worth emitting.

Alternatives considered:

- **Sidecar `notification_read_state` table.** A second key, a join on every
  inbox query and a nullable-forever left join, for a single timestamp whose
  lifetime is tied to the row. The pure-consumer seam is about *who may write
  which fields*, not about table shape. Rejected.
- **Read state as event-derived data** (a `notification.read` event feeding a
  projection). Adds a producer, a consumer and a contract for UI state that no
  other surface consumes; read flags would still be per-user mutable state.
  Rejected.
- **Auto-mark-on-view.** Makes the badge lie whenever a user merely glances at
  the dropdown; the inbox spec pins explicit mark-read actions. Rejected.
- **Auditing reads.** Read state changes no PII and no business fact; the
  security checklist answers "no audit entry" explicitly. Rejected.
- **A partial unread index now.** The composite inbox index already serves the
  unread filter at v1 scale; a partial index is a later, parity-checked
  optimisation. Deferred.

## Decision

1. **One column.** `notifications.read_at timestamptz NULL` — `NULL` means
   unread; the first mark-read sets the timestamp and later calls keep it.
   Migration `0009_*` adds the column and the index below. The domain
   `Notification` value gains a defaulted `read_at`; `NewNotification` and the
   worker are untouched, so worker rows are born unread.
2. **A narrow carve-out.** Read state is user-owned mutable state layered on
   the worker-written projection. Only `read_at` may be written by the HTTP
   path — never `subject`, `body`, `kind`, recipient or event fields, and
   never anything on `notification_deliveries`. The carve-out sets the
   precedent: a future user-owned overlay needs its own ADR, not a quiet
   widening of this one.
3. **Rebuilds may drop read flags.** Accepted: they are not event-derived. The
   model docstring says so, so a reviewer never has to infer it.
4. **No audit entry and no event for read-state mutations.** They change no
   PII and no business fact (`.ai/security.md` checklist); the trigger
   mutation that produced the notification is already audited.
5. **Idempotence in one statement.** `mark_read` updates
   `WHERE read_at IS NULL` and reports whether it wrote; `mark_all_read`
   updates only the recipient's currently-unread rows, so already-read rows
   keep their timestamp and rows created after the statement stay unread.
   Nothing commits inside the repository; the request session does.
6. **Ownership is a policy decision.** Repository reads are tenant-scoped;
   `domains/notifications/policies.py` enforces
   `tenant_id == identity.tenant_id` and
   `recipient_employee_id == identity.employee_id` before a row is marked.
   Unknown, foreign and cross-tenant ids are one `404
   notification_not_found` (never leak existence). Read state follows the
   account's current employee link; stored rows are never re-addressed.
7. **Index.** `ix_notifications_inbox` on
   `(tenant_id, recipient_employee_id, created_at, id)` serves the inbox's only
   access path — one recipient's rows newest-first — and the unread filter on
   the same prefix. It is declared identically in the ORM `__table_args__` and
   the migration (ADR 0007 precedent).

## Consequences

- The inbox works with no worker and no realtime: a missed badge update heals
  on the next pull, because the count is one indexed query.
- Read flags are disposable. A projection rebuild from the event stream may
  lose them without corrupting anything a user relies on for correctness.
- The HTTP write path gains exactly one permitted column; the notification
  projection and its delivery ops data stay worker-owned.
- A partial `WHERE read_at IS NULL` index can be added later under the
  partial/covering parity rule without changing this contract.
