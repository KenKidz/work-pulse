# Feature: Notification inbox (bell + read state)

- **Status:** done
- **Phase:** 3
- **Size:** M
- **Surfaces:** api+web
- **Links:** `notifications.md` (the delivery substrate — ADR 0015) ·
  `.ai/domain.md` (pure-consumer seam) · `.ai/security.md` (own-only, PII) ·
  ADR 0010 (session-resolved identity, `employee_id` link) ·
  `.kilo/skills/workpulse-ui/SKILL.md`

## Problem

Notifications are delivered out-of-band only — email and Slack
(`notifications.md`). Nothing in the product shows them: a user who has no
Resend key / Slack channel configured (every dev environment) sees nothing at
all, and even with providers configured the message of record lives in the
`notifications` table where only SQL inspection can find it. The rows already
carry exactly what an inbox needs (subject, body, kind, recipient, created_at);
this feature exposes them as the person-addressed inbox the spec deferred
("the durable substrate for one").

## Business rules

The rules the code must obey. If a rule is not written here, it does not exist.

1. **Own-only, every role.** A caller sees exactly the notifications addressed
   to their own linked employee (`recipient_employee_id ==
   Identity.employee_id`). There is no capability for reading someone else's
   inbox — not for managers, not for HR, not for admins: notifications are
   person-addressed (ADR 0015), and `.ai/security.md`'s matrix grants "own"
   reads only. Access to a foreign id is `404 notification_not_found`, never a
   revealing `403` (AGENTS.md rule 12).
2. **Tenant-scoped repository** (AGENTS.md rule 6): every query filters
   `tenant_id` from the session identity; cross-tenant access is `404`.
3. **Projection rows stay worker-written.** The `Notification`/`Delivery`
   data is the event-rebuildable projection (`.ai/domain.md` pure-consumer
   rule). Read state is **user-owned mutable state layered on the row**, not
   projection data: the HTTP path may write only the read-state column(s) and
   nothing else — never `subject`, `body`, `kind`, `recipient_*`, `event_*`,
   or any `notification_deliveries` field. A rebuild from the event stream may
   drop read flags (accepted; they are not event-derived).
4. **Read semantics.** Marking one notification read is idempotent: the first
   call sets `read_at`, later calls keep the original timestamp. "Mark all
   read" marks exactly the recipient's unread rows **at call time** and is a
   no-op when none exist; a row created afterwards stays unread.
5. **Message of record, verbatim.** The inbox shows the stored subject/body as
   rendered at consume time — never re-rendered, never enriched at read time.
6. **No delivery status in the inbox.** `status`, `attempts`, `last_error` are
   ops data (ADR 0015); they appear in neither the API responses nor the UI.
7. **Read-state mutations are not sensitive.** They change no PII and no
   business fact, so they write **no audit entry** (`.ai/security.md`
   checklist answer) and emit **no event**.
8. **Pull-based freshness.** The header badge reflects `unread_count` after
   any read mutation and on window focus. This is not a realtime surface: no
   WebSocket, no live push of arriving notifications (workpulse-ui).

## Scope

- Migration `0009_*`: read state on the notification row — a nullable
  `read_at` (timestamptz) on `notifications`, written only by the API path
  (rule 3). Any index added follows the partial/covering parity rule (declare
  in both the migration and `__table_args__`).
- Read-side capability in `apps/api/app/domains/notifications/`: list /
  unread-count / mark-read / mark-all-read service methods, plus the domain's
  first `policies.py` (own-only guard, rule 1).
- Router `apps/api/app/api/v1/notifications.py` — four routes, contract pinned
  below; `make gen-api` regenerates `@workpulse/api-client`.
- Web: app-shell **bell + unread badge** opening a dropdown of the most recent
  notifications (with a "View all" link), and `/notifications` — a paged
  newest-first list with read/unread distinction, per-item mark-read,
  mark-all-read, and empty/error states per workpulse-ui.
- Model docstring updates in `models_notifications.py` (the rule-3 carve-out)
  in the same change.
- Tests per surface: unit (read-state semantics, own-only policy),
  integration (repository tenancy + ownership), e2e (fixture-created rows —
  the notification worker is **not** in the e2e hop; delivery is already
  proven at the worker level).

## Out of scope

- Per-user preferences, muting, digesting, filtering by kind.
- Delete / archive of notifications.
- Delivery status, provider errors or replay tooling in the UI (ops only).
- Live push of arriving notifications (WebSocket/toast) — badge is pull-based.
- Deep links from a notification to the related leave request: bodies are
  plain text and carry no structured reference; a follow-up if wanted.
- Re-addressing stored rows when an account's `employee_id` link changes.
- `notification.*` events — none are emitted (unchanged from `notifications.md`).
- Email password-reset / user-invite flows — identity features consuming the
  delivery capability (`identity-access.md` decision 4).
- Mobile-only layouts beyond the existing responsive shell rules.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/notifications/` (extend: read-side service methods + new `policies.py`; models/schemas gain read-state value(s)) |
| API router | `apps/api/app/api/v1/notifications.py` |
| Persistence | `apps/api/app/infrastructure/postgres/` (`models_notifications.py` + notification repository read/mark methods) · Alembic `0009_*` |
| Events | — (none; `contracts/events/` unchanged) |
| Workers | — (`workers/notifications/` untouched — rule 3) |
| Web feature | `apps/web/features/notifications/` (queries/mutations via `@workpulse/api-client`, bell + dropdown + list pieces) |
| Web routes | `apps/web/app/(dashboard)/notifications/` · bell integrated into the app shell header |
| API tests | `apps/api/tests/unit` (read semantics, own-only policy) · `tests/integration` (tenancy, ownership 404, idempotence) |
| E2E | `tests/e2e/notification-inbox.spec.ts` (rows via fixture/seed helper, no worker in the hop) · `full-hop.sh` fixture hook if needed |

## Roles & access

- Every authenticated role — Employee, Manager, HR, Finance, Admin,
  Super Admin — may call all four routes, **own data only** (rule 1). One row
  is added to `.ai/security.md`'s matrix: *View own notification inbox & mark
  read* → `own` for all roles.
- All routes require a session (`401` otherwise). The mutating routes'
  denied case is ownership, not role: a non-owner gets `404` and no state
  change (rule 1) — covered by a dedicated test.
- An identity without a linked `employee_id` (account-only user) has an empty
  inbox: list `[]`, count `0`, read-all no-op.

## API contract

**Final — pinned by T1, 2026-10-21** (full-stack rule: pinned before coding).
Uniform errors; unknown or foreign
ids answer `404 {"error": {"code": "notification_not_found"}}` — one code for
"doesn't exist" and "not yours", so the API never leaks existence.

| Method & path | operationId (orval hook) | Input | Response |
| --- | --- | --- | --- |
| `GET /api/v1/notifications` | `listNotifications` | `?page=&page_size=` | `200 {"items": NotificationItem[], "page": Page}` |
| `GET /api/v1/notifications/unread-count` | `getNotificationUnreadCount` | — | `200 {"unread_count": int}` |
| `POST /api/v1/notifications/{id}/read` | `markNotificationRead` | — | `204` (idempotent) |
| `POST /api/v1/notifications/read-all` | `markAllNotificationsRead` | — | `204` (idempotent) |

Pagination matches the house shape (`PageParams`, `app/shared/pagination.py`):
`page` ≥ 1, `page_size` default 20, max 100 — out-of-range integers clamp, only
non-integer input answers `422 validation_error`; `Page` as in `leave.md`.
Ordering is `created_at` DESC with `id` as tiebreak.

```jsonc
NotificationItem {
  "id", "tenant_id",
  "kind": "leave_requested|leave_approved|leave_rejected|leave_withdrawn|attendance_late",
  "subject": string,          // stored message of record (may contain personal names)
  "body": string,             // stored message of record (may contain personal names)
  "created_at": date-time,
  "read_at": date-time|null   // null = unread
}

{ "unread_count": int }       // GET /notifications/unread-count
```

Validation failures use the standard `422` shape (`page_size` out of range,
non-integer `page`). No route exposes deliveries, recipients' account data, or
event ids.

## Events

None. This is a read-side feature over existing rows: no producer, no
consumer, `contracts/events/` unchanged. `make gen-api`'s diff is exactly the
new routes and their generated client hooks.

## Edge cases

- Identity with no linked `employee_id` → empty list, `unread_count: 0`,
  read-all returns `204` as a no-op.
- Account re-linked to a different employee later → the inbox follows the
  current link; rows addressed to the old person are simply not visible.
- Mark-read twice → second call keeps the original `read_at` (rule 4).
- Mark-read for another user's or another tenant's id → `404`, state
  unchanged (rules 1–2).
- Read-all races a newly created notification → the new row stays unread
  (rule 4).
- Non-integer pagination (`page_size` "x") → `422 validation_error`;
  out-of-range integers clamp per `PageParams` (0→1, 501→100).
- Empty inbox → EmptyState per workpulse-ui, not an empty table.
- List/API failure → the page shows its error state and the bell degrades
  quietly (badge hidden, no crash).
- Bodies contain personal names by design (message of record, ADR 0015
  decision 6) but never free text (`reason`, `decision_note`); log lines carry
  notification ids only — the PII sweep stays green.

## Acceptance criteria

- [x] API (list): given two users in one tenant with fixture notifications, when each lists, then each receives only rows addressed to their own employee, newest first, paginated, with the stored subject/body/kind and `read_at`.
- [x] API (read): given unread rows, when the owner marks one read, then `read_at` is set and `unread_count` drops by exactly 1; a second mark-read keeps the original `read_at` (unit + integration).
- [x] API (read-all): given k unread among already-read rows, when the owner marks all read, then `unread_count` is 0, previously-read rows keep their `read_at`, and a row created after the call remains unread.
- [x] API (denied/ownership): given a notification addressed to user B, when user A marks it read (single or via read-all scope), then `404 notification_not_found` and B's row is unchanged; an unauthenticated call gets `401`.
- [x] API (tenant isolation): given tenant A's rows, when tenant B lists or reads counts, then no row of tenant A appears, and marking A's id answers `404`.
- [x] API (failure): non-integer `page_size` or `page` → `422`; unknown id → `404 notification_not_found`.
- [x] API (link-less identity): an account without `employee_id` gets `[]`, `unread_count: 0`, and read-all is a `204` no-op.
- [x] Audit/events: read-state mutations write no audit entry and emit no event; `contracts/events/` is unchanged and `make gen-api`'s diff is exactly the new routes.
- [x] Web: the header bell shows the unread count; after mark-read or mark-all on `/notifications` (or from the dropdown) the badge and list update without a reload; the dropdown lists recent notifications and links to `/notifications`.
- [x] Web (states): unread rows are visually distinct from read rows; an empty inbox renders the empty state; an API failure renders the error state without breaking the shell.
- [x] Unit: read-state semantics (idempotent mark-read, read-all set semantics) and the own-only policy run without I/O and without wall-clock sleeps.
- [x] E2E: with fixture-created rows (no notification worker in the hop), a real login sees the badge count, opens `/notifications`, marks all read, and the badge drops to 0 with read styling shown; the existing suite stays green.

## Decisions (PO, 2026-10-21)

Scope answers (feature kickoff):

1. **UI shape: header bell + `/notifications` page** — badge + dropdown of
   recent items in the app shell (the account-dropdown precedent), and a full
   paged page behind it. (Chosen over page-only / bell-only.)
2. **v1 read features: per-item mark-read + mark-all-read + header unread
   badge.** Not auto-mark-on-view: explicit actions keep the badge honest when
   the user only glances at the dropdown.
3. **Own-only for every role** — confirmed in kickoff; person-addressed rows
   and the matrix's "own" reading make team/admin inbox access a future
   capability, not a default.

Implementation defaults (not product scope; cheap to revisit at `/plan`):

4. Read state as a nullable `read_at` column on `notifications` (migration
   `0009`) rather than a sidecar table — the rule-3 seam is about who may
   write which fields, not about table shape; a rebuild may drop read flags.
5. No audit entries for read-state mutations (rule 7): they are UI state, not
   sensitive data — the `.ai/security.md` checklist is answered explicitly so
   a reviewer does not have to infer it.
6. No delivery status in the inbox (rule 6): ops data stays out of user
   surfaces (rule 6).
7. Pagination `page`/`page_size` (house shape) and pull-based badge refresh
   (query invalidation + window focus) — matching `leave.md` and the wall's
   REST refresh respectively.
8. E2E rows come from a fixture/seed helper, never the worker; the worker
   stays out of the e2e hop (its delivery contract is already proven at the
   worker level).

## Plan

Planned 2026-10-21. **Contract-first:** T1 pins the API contract and the
security-matrix row in this spec; there are **no event contracts** to write
(this is a read-side feature — `contracts/events/` stays untouched), so the
backend (T2–T4) builds directly against the pinned HTTP contract and the web
(T5) consumes only the regenerated `@workpulse/api-client`; T6 lands the e2e
flow and the records. Every task ends with `make check` green. One contract
clarification was recorded at plan time (amended in the contract, edge cases
and ACs above): pagination follows the pinned `PageParams` house shape —
`page` clamped ≥ 1, `page_size` default 20 / max 100 with out-of-range integers
clamped, only non-integer input answers `422 validation_error`. The draft's
"1..50 → 422" reading misdescribed the house shape it defers to; decided at
`/plan` (PO) in favour of `app/shared/pagination.py` consistency.

### Acceptance criteria → where they are satisfied

| AC | Landed by | Proof |
| --- | --- | --- |
| API (list): own rows only, newest first, paginated, stored subject/body/kind + `read_at` | T3, T4 | `tests/integration/test_notifications_inbox.py` (two users, one tenant) |
| API (read): mark one → `read_at` set, `unread_count` −1; second call keeps original `read_at` | T2–T4 | `tests/unit/notifications/test_inbox_service.py` + `test_notifications_inbox.py` |
| API (read-all): k unread → 0; already-read rows keep `read_at`; a later row stays unread | T2–T4 | unit + `test_notifications_inbox.py` |
| API (denied/ownership): foreign id → `404 notification_not_found`, B's row unchanged; unauth → `401` | T3, T4 | `tests/unit/notifications/test_policies.py` + `test_notifications_inbox.py` |
| API (tenant isolation): no cross-tenant rows in list/count; marking A's id from B → `404` | T2, T4 | `tests/integration/test_notifications_repository.py` + `test_notifications_inbox.py` |
| API (failure): non-integer `page_size`/`page` → `422`; unknown id → `404 notification_not_found` | T4 | `test_notifications_inbox.py` |
| API (link-less identity): `[]`, `unread_count: 0`, read-all `204` no-op | T3, T4 | unit + `test_notifications_inbox.py` |
| Audit/events: no audit entry, no event; `contracts/events/` unchanged; `make gen-api` diff = new routes only | T3 (no audit/outbox wired), T4 | `test_notifications_inbox.py` (audit table empty after mutations) + `make gen-api` diff |
| Web: bell badge; badge + list update without reload after mark-read/mark-all; dropdown lists recent and links to `/notifications` | T5 | `apps/web/tests/notifications/` |
| Web (states): unread distinct from read; empty state; error state without breaking the shell | T5 | `apps/web/tests/notifications/` |
| Unit: read semantics + own-only policy, no I/O, no wall-clock sleeps | T3 | `tests/unit/notifications/` (fixed `NOW`, injected `now`) |
| E2E: fixture rows (no worker in the hop) → badge → `/notifications` → mark all → badge 0 + read styling; suite stays green | T6 | `tests/e2e/notification-inbox.spec.ts` |

### T1 — Pin the API contract + security matrix (docs only)

- Finalize `## API contract` in this spec: header → "final — pinned by T1,
  2026-10-21"; keep the four operationIds (`listNotifications`,
  `getNotificationUnreadCount`, `markNotificationRead`,
  `markAllNotificationsRead`) and the `NotificationItem` shape; record the
  plan-time pagination clarification (amended above).
- `.ai/security.md`: one matrix row — *View own notification inbox & mark
  read* → `own` for all six roles — plus the rule-7 checklist answer: read-state
  mutations write **no audit entry** and emit no event.
- No code.

Verify: docs diff only; `make check` unaffected (first full run at T2).

### T2 — Persistence: `read_at` · migration 0009 · repository read/mark methods

- `apps/api/app/infrastructure/postgres/models_notifications.py`:
  `NotificationRow` gains `read_at: Mapped[datetime | None]`
  (DateTime(timezone=True), nullable); docstring gains the rule-3 carve-out —
  read state is user-owned mutable state layered on the projection row, written
  only by the API path; a rebuild from the event stream may drop it. New index
  `ix_notifications_inbox` on `(tenant_id, recipient_employee_id, created_at,
  id)` in `__table_args__` (serves list ordering and the unread filter).
- Migration `apps/api/alembic/versions/0009_notification_read_state.py`
  (`revision="0009", down_revision="0008"`): `op.add_column("notifications",
  sa.Column("read_at", …, nullable=True))` + `op.create_index(...)` with the
  exact `__table_args__` definition (partial/covering parity rule).
- `apps/api/app/domains/notifications/models.py`: `Notification` gains
  `read_at: datetime | None = None`; `NewNotification` stays unchanged — worker
  rows are born unread and the worker never sees read state.
- Read methods added to the `NotificationRepository` protocol
  (`domains/notifications/repository.py`) and the adapter
  (`infrastructure/postgres/repositories/notifications.py`), all tenant-scoped,
  no commits inside (the request session commits):
  `list_for_recipient(tenant_id, recipient_employee_id, page) ->
  (Sequence[Notification], int)` ordered `created_at` DESC, `id` DESC;
  `count_unread(tenant_id, recipient_employee_id) -> int`;
  `get(tenant_id, notification_id) -> Notification | None`;
  `mark_read(tenant_id, notification_id, *, read_at) -> bool` — conditional
  `WHERE read_at IS NULL` so idempotence holds in one statement;
  `mark_all_read(tenant_id, recipient_employee_id, *, read_at) -> int` —
  `WHERE … AND read_at IS NULL` only, so already-read rows keep their timestamp
  and rows created after the statement stay unread.
- Tests: extend `tests/integration/test_notifications_repository.py` —
  tenancy + ownership visibility (`LookupError`-style visibility on foreign
  ids, per the existing `set_delivery_status` convention), idempotent
  `mark_read`, `mark_all_read` set semantics including a row created "after".

Verify: `make check-api` green — worker tests still pass untouched
(`NewNotification` unchanged).

### T3 — Domain: read service · `policies.py` · unit tests

- `apps/api/app/domains/notifications/schemas.py`: HTTP read models —
  `NotificationItem` (`id`, `tenant_id`, `kind`, `subject`, `body`,
  `created_at`, `read_at: datetime | None`) built by explicit mapping from
  `Notification`, never exposing `event_*`, `recipient_*` or any delivery field
  (rule 6); `NotificationListPage {items, page: Page}`;
  `NotificationUnreadCount {unread_count: int}`.
- `apps/api/app/domains/notifications/exceptions.py`: `NotificationNotFoundError`
  — `status_code=404`, `code="notification_not_found"`, one code for unknown,
  foreign and cross-tenant ids (never leak existence). Module docstring updated
  ("no HTTP surface" no longer holds for the read side).
- `apps/api/app/domains/notifications/policies.py` (new, the domain's first):
  `ensure_own(identity, notification)` — raises `NotificationNotFoundError`
  unless `notification.tenant_id == identity.tenant_id` **and**
  `notification.recipient_employee_id == identity.employee_id`; fail closed,
  a link-less identity can never pass. Guard lives here, never in the router
  (rule 12).
- `apps/api/app/domains/notifications/service.py`: `NotificationInboxService(repo)`
  alongside the delivery service — `list(identity, page) ->
  NotificationListPage`, `unread_count(identity) -> int`,
  `mark_read(identity, notification_id, now) -> None` (load via `get` →
  `ensure_own` → conditional `mark_read`), `mark_all_read(identity, now) -> int`.
  `now` is caller-supplied (house clock convention — no wall clock in the
  domain). Link-less identity (`employee_id is None`) short-circuits: empty
  page with total 0, count 0, read-all no-op. Deliberately **no `AuditWriter`,
  no outbox, no `commit`** (rule 7; comment it).
- Tests: `tests/unit/notifications/test_inbox_service.py` and
  `test_policies.py` — extend `tests/unit/notifications/fakes.py` with seeded
  rows + the new read/mark methods; fixed `NOW`, injected `now`, no sleeps.

Verify: `make check-api` green.

### T4 — Router · DI · integration tests · regenerate clients

- `apps/api/app/api/v1/notifications.py`: the four routes exactly per the
  pinned contract (literal paths `unread-count` / `read-all` registered before
  the `/{id}/read` param route for hygiene); `IdentityDep` +
  `NotificationInboxServiceDep` + `PageParamsDep`; thin bodies (identity →
  service); `204` on both mutations; `response_model` on the GETs.
  `domains/notifications/__init__.py` docstring updated (an HTTP read surface
  now exists).
- `apps/api/app/dependencies.py`: `get_notification_inbox_service(session)`
  wiring `PostgresNotificationRepository(session)` + `NotificationInboxServiceDep`;
  comment that no audit/outbox is wired on purpose (rule 7).
- `apps/api/app/api/v1/__init__.py`: `api_router.include_router(
  notifications.router, tags=["notifications"])`.
- Tests: `tests/integration/test_notifications_inbox.py` via
  `client_factory` — every route-level AC: own-only list (two users, one
  tenant), idempotent mark-read (`read_at` kept, count −1 exactly once),
  read-all semantics (row created after stays unread), foreign + cross-tenant
  ids → `404 notification_not_found` with the other user's row unchanged,
  unauth → `401`, non-integer pagination → `422 validation_error`, link-less
  identity, and the rule-7 proof (no `audit_entries` row after mutations).
- `make gen-api` — the diff must be exactly the new routes and their generated
  hooks (`useListNotifications`, `useGetNotificationUnreadCount`,
  `useMarkNotificationRead`, `useMarkAllNotificationsRead`); resolve orval
  formatter churn once (leave T6 precedent) if it appears.

Verify: `make check-api` green and the `make gen-api` diff reviewed
(routes + hooks only).

### T5 — Web: bell + dropdown + `/notifications` page

- `apps/web/features/notifications/` (leave's layout): `api.ts` (orval query
  keys + `invalidateNotifications(queryClient)`), `hooks/use-notifications.ts`
  (thin wrappers over the generated hooks; `useUnreadCount` opts into
  `refetchOnWindowFocus: true` — the global QueryClient disables it
  (`providers.tsx`), and this is the spec's pull-based badge freshness; both
  mutations invalidate list + count so badge and list update without a reload),
  `labels.ts`, `components/NotificationBell.tsx`,
  `components/NotificationsList.tsx`.
- `NotificationBell`: the 44px ghost `Button` + Bell icon + unread count badge
  (mono/tabular-nums) in the header right cluster; `DropdownMenu` following the
  `UserMenu` precedent (align="end") with the most recent items in a bounded
  scroll pane (`max-h-[min(60dvh,36rem)] overflow-y-auto`), unread markers that
  are never color-alone (icon/label per workpulse-ui §6), and a "View all" item
  linking to `/notifications`; a failed count query degrades quietly (badge
  hidden, no toast, no crash).
- `NotificationsList`: paged newest-first list with house pagination controls,
  per-item mark-read + mark-all-read, unread/read distinction (badge/label +
  weight change), `EmptyState` on empty, inline error state with retry on
  failure; no delivery status anywhere (rule 6).
- `apps/web/app/(dashboard)/notifications/page.tsx` (metadata + `PageHeader` +
  list) and `apps/web/app/(dashboard)/layout.tsx` (bell into the right cluster
  before `ThemeToggle`).
- Tests: `apps/web/tests/notifications/` (helpers per
  `apps/web/tests/leave/helpers.tsx`) — badge updates after mark-all without
  reload, dropdown links to `/notifications`, unread vs read styling, empty
  state, error state.

Verify: `make check-web` green.

### T6 — E2E · records

- `tests/e2e/full-hop.sh`: `reset_notification_fixtures()` hook (inline
  Python, mirroring `reset_leave_fixtures`) inserting a few unread
  `notifications` rows addressed to the seeded worker
  (`WORKER_EMPLOYEE_ID` in `tests/e2e/auth.ts`); the notifications worker stays
  out of the hop — delivery is already proven at the worker level.
- `tests/e2e/notification-inbox.spec.ts` (`describe.serial`, `uiLogin` from
  `auth.ts`): badge shows the seeded unread count → `/notifications` lists them
  with unread styling → per-item mark-read drops the badge by one → mark-all
  read drives the badge to 0 with read styling shown; the existing suite stays
  green.
- Records (same change): tick the AC boxes above, `Status: done`,
  `tasks/TODO.md` row → Done with gate counts, `.ai/features/README.md`
  backlog row → done.

Verify: full `make check` green.

### Risks, unknowns & decisions that deserve an ADR

1. **ADR 0016 recommended (land with T2) — user-owned read state on a
   worker-written projection.** `read_at` is the only field the HTTP path may
   write on `notifications`; a rebuild from the event stream may drop read
   flags. It is the first deliberate carve-out of the pure-consumer rule
   (`.ai/domain.md`) and sets a precedent for future user-owned overlays, so it
   deserves a short ADR even though the column itself is cheap to reverse.
2. **Pagination contract clarified at plan time** — house `PageParams` (clamp,
   max 100, non-integer → 422) instead of the draft's "1..50 → 422" reading;
   decided by the PO at `/plan` and amended in the spec above. The route adds
   no validation dependency of its own.
3. **Window-focus badge refresh is a per-query opt-in** —
   `refetchOnWindowFocus: false` is global in `apps/web/app/providers.tsx`;
   only `useUnreadCount` opts in. If the badge is later expected to refresh the
   list too, that is a new decision, not a bug.
4. **Unread-count access path** — one composite index
   `(tenant_id, recipient_employee_id, created_at, id)` covers list ordering
   and the unread filter at this scale; a partial index
   (`WHERE read_at IS NULL`) is deferred and must follow the parity rule when
   added.
5. **orval regeneration churn** — T4 pins the expectation that `make gen-api`'s
   diff is exactly the new routes + hooks (an AC); any formatter churn is
   resolved once inside T4 (leave T6 precedent) before T5 builds on the client.
6. **Concurrent mark-read** — the conditional `WHERE read_at IS NULL` write
   keeps the original timestamp under races; mark-all's statement snapshot
   leaves concurrently-inserted rows unread (rule 4's "at call time" reading).
7. **Rebuild drops read flags** — accepted (rule 3/decision 4); the model
   docstring (T2) says so explicitly so a reviewer never has to infer it.

### Test strategy

| Layer | Added / changed | Gate | Failure case covered |
| --- | --- | --- | --- |
| Unit | `tests/unit/notifications/test_inbox_service.py`, `test_policies.py`, extended `fakes.py` | `make check-api` | idempotent mark-read, read-all set semantics (row created after stays unread), own-only policy denial (incl. link-less identity), no wall clock |
| Integration (repo) | `tests/integration/test_notifications_repository.py` | `make check-api` | cross-tenant invisibility, foreign-id visibility, `mark_read` idempotence under one statement |
| Integration (routes) | `tests/integration/test_notifications_inbox.py` | `make check-api` | `404 notification_not_found` (unknown/foreign/cross-tenant), `401` unauth, `422` non-integer pagination, link-less identity empty shapes, no audit row after mutations |
| Web unit | `apps/web/tests/notifications/` | `make check-web` | badge/list refresh after mutations, empty state, error state degrading quietly, unread/read distinction |
| E2E | `tests/e2e/notification-inbox.spec.ts` + `full-hop.sh` fixture hook | `make check-e2e` (inside `make check`) | fixture-seeded inbox → mark all → badge 0 with read styling; suite stays green with the notification worker stopped |

Mandatory per `.ai/testing.md`: happy path + ≥1 failure case per surface,
tenant isolation, denied-case coverage for every mutating route (here: the
ownership `404`, since all roles are allowed), no wall-time in unit tests. Full
`make check` at the end of every task.
