# Feature: Attendance

- **Status:** approved
- **Phase:** 1
- **Size:** M
- **Surfaces:** api+web+events (contracts only in Phase 1)
- **Links:** `.ai/domain.md` (Attendance context, presence state machine) · depends on `organizations.md`, `employees.md`

## Problem

This is the feature that makes WorkPulse a *real-time workforce* platform rather
than an HR database. A check-in must become a durable record, a presence state,
a lateness judgement and — from Phase 2 — a push to the manager's board. Getting
the record and its invariants right in Phase 1 is what makes the realtime and
analytics phases cheap later.

## Business rules

- An employee has **at most one open attendance record** (checked in, not
  checked out) at any time.
- A check-out requires an open record and closes it.
- **Server time is authoritative** (proposed): client-supplied timestamps are
  rejected in Phase 1.
- The workday a record belongs to is the calendar date of the check-in **in the
  employee's location timezone**.
- Lateness: `checked_in_at > shift.start_time + shift.grace_minutes`, evaluated
  in the location timezone. The `late` flag is set once at check-in and never
  cleared.
- An employee with no shift configured is never marked late (and the board
  reports them as present).
- Terminated employees cannot check in or out.
- `work_mode` is `onsite` or `remote` — the board counts them separately.
- Idempotency: a repeated check-in while a record is open is rejected, not
  duplicated.
- The board is request/response in Phase 1 (the client may poll); WebSocket push
  arrives in Phase 2.
- `absent` on the board means: an employee whose expected shift started today
  (their location's shift, else the tenant default) with no check-in by
  `start + grace`. Phase 1 has no rosters or leave data, so this over-counts on
  rest days — the UI must say so.
- Every check-in/check-out writes an audit entry and produces an event contract.

## Scope

- Check-in / check-out endpoints.
- Attendance records list with filters (employee, date range) and pagination.
- **Live workforce board**: counts (`present`, `remote`, `late`, `on_leave`,
  `absent`, `checked_out`) + a recent-activity feed.
- Minimal **Shift** reference data (name, start, end, grace minutes, optional
  location scope) — the smallest thing that makes lateness real.
- Event contracts under `contracts/events/attendance/`.
- Web: a "Check in / Check out" action + the live board page.

## Out of scope

- **Correction requests** and their approval workflow (Phase 3, with Leave-style
  state machines).
- QR codes, geo-fencing, hardware terminals, mobile capture.
- Overtime calculation (Phase 3–4, Payroll).
- Shift scheduling / rota planning, absence planning, leave calendar.
- WebSocket push (Phase 2) and analytics aggregation (Phase 4).
- `on_leave` counts are always `0` until Leave lands in Phase 3 — the field
  exists so the board contract does not change later.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/attendance/` |
| API router | `apps/api/app/api/v1/attendance.py` |
| Shift reference data | `apps/api/app/domains/attendance/` (decided — see `## Decisions`) |
| Events | `contracts/events/attendance/attendance-*.v1.json` |
| Web feature | `apps/web/features/attendance/` |
| Web routes | `apps/web/app/(dashboard)/attendance/` |
| API tests | `apps/api/tests/unit` (clock injected) · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/` |

## Roles & access

| Capability | Employee | Manager | HR | Admin |
| --- | --- | --- | --- | --- |
| Check in / out (self) | own | own | own | own |
| Read own records | own | own | own | own |
| Read team records + board | — | team | Y | Y |
| Manage shifts | — | — | Y | Y |

## API contract (proposed)

| Method & path | Input | Response |
| --- | --- | --- |
| `POST /api/v1/attendance/check-ins` | `{"work_mode": "onsite|remote", "note"?}` | `201 AttendanceRecord` |
| `POST /api/v1/attendance/records/{id}/check-out` | `{"note"?}` | `200 AttendanceRecord` |
| `GET /api/v1/attendance/records` | `?employee_id=&from=&to=&late=&page=&page_size=` | `200 {"items": AttendanceRecord[], "page": Page}` |
| `GET /api/v1/attendance/board` | `?date=YYYY-MM-DD` (default: today, tenant tz) | `200 Board` |
| `GET /api/v1/shifts` · `POST /api/v1/shifts` · `PATCH /api/v1/shifts/{id}` | `Shift` | `200/201 Shift` |

```json
AttendanceRecord {
  "id", "tenant_id", "employee_id", "workday": "2026-09-24",
  "checked_in_at", "checked_out_at": null, "work_mode": "onsite",
  "late": false, "shift_id": null, "note": null, "created_at"
}

Board {
  "date": "2026-09-24",
  "counts": { "present": 0, "remote": 0, "late": 0, "on_leave": 0,
              "absent": 0, "checked_out": 0 },
  "activity": [ { "occurred_at", "employee_id", "action": "checked_in" } ]
}

Shift { "id", "tenant_id", "name", "start_time": "09:00", "end_time": "18:00",
        "grace_minutes": 10, "location_id": null }
```

Error codes: `attendance_already_checked_in` · `attendance_not_open` ·
`attendance_record_not_found` · `employee_not_active` · `shift_not_found` ·
`invalid_work_mode`.

## Events

Contracts in Phase 1, published in Phase 3:

| `event_type` | Emitted when | Payload |
| --- | --- | --- |
| `attendance.checked_in` | a record opens | `attendance_id`, `employee_id`, `workday`, `work_mode`, `late` |
| `attendance.checked_out` | a record closes | `attendance_id`, `employee_id`, `workday`, `duration_minutes` |
| `attendance.late` | check-in is judged late | `attendance_id`, `employee_id`, `workday`, `shift_id`, `minutes_late` |

## Edge cases

- Check-out with no open record → `409 attendance_not_open`.
- Second check-in while open → `409 attendance_already_checked_in`.
- Check-in by a terminated employee → `409 employee_not_active`.
- Check-in at 23:58, check-out at 00:10 → both belong to the check-in workday.
- Location timezone differs from the tenant's → board date uses the requested
  date, lateness uses the location timezone.
- No shift configured → `late` stays `false`.
- Check-in exactly at `start + grace` → **not** late (boundary is exclusive).
- Cross-tenant board request → `404`/empty, never another tenant's counts.
- Concurrent double check-in (two tabs) → one record, one `409`.

## Acceptance criteria

- [ ] API: check-in returns `201` with an open record and the correct workday for the location timezone.
- [ ] API: check-out closes the record and reports `duration_minutes`.
- [ ] API: a check-in after `shift.start + grace` sets `late: true` and produces an `attendance.late` contract payload.
- [ ] API (failure): double check-in → `409 attendance_already_checked_in`.
- [ ] API (failure): check-out without an open record → `409 attendance_not_open`.
- [ ] API (failure): check-in by a terminated employee → `409 employee_not_active`.
- [ ] API (tenant isolation): tenant B's board shows none of tenant A's records.
- [ ] API (denied role): an `Employee`-role request to another employee's records → `403`/`404`.
- [ ] Unit tests use an injected clock — no wall-time assertions.
- [ ] Contracts: the three event schemas exist and validate produced payloads.
- [ ] Audit: each check-in/check-out wrote an audit entry.
- [ ] Web: check in → the board counts and activity feed update on refresh; a `409` renders as a visible error state.

## Decisions (PO, 2026-09-24)

1. **Minimal Shift ships in Phase 1** (confirmed by the product owner) — lateness
   is part of the product's value, and retrofitting it would change the board
   contract.
2. **Shift lives in `domains/attendance/`**, exposed as `/api/v1/shifts`. Shifts
   exist to judge attendance; Organization owns structure, not time rules.
3. **Server time is authoritative; client-supplied `occurred_at` is rejected.**
   Backdated presence is a trust problem, not a convenience. If offline or mobile
   capture arrives later, it comes through a signed correction flow (Phase 3),
   not a client timestamp.
4. **`work_mode` (onsite/remote) is in Phase 1** — the board's Remote count
   requires it.
5. **Correction requests wait for Phase 3**, where the approval workflow and its
   state machine already exist.
6. **No QR/geo/hardware capture in Phase 1** — web button only.
7. **`absent` = an employee whose expected shift started today (their location's
   shift, else the tenant default) with no check-in by `start + grace`.**
   Accepted limitation: Phase 1 has no rosters and no leave data, so this
   over-counts on rest days and cannot subtract leave until Phase 3. The board
   documents this in the UI copy.
8. Identity is the **dev identity** (ADR 0004): self check-in resolves from
   `X-Actor-Id`; board/records access is role-checked in `policies.py`.

## Plan

Planned 2026-09-28. **Contract-first:** T1 pins the API contract in this spec
(contract deltas are decided there, not during the build), T2 writes the three
event contracts before any producer/consumer code, the backend (T3–T5) is built
against them, and the web (T6) consumes only the regenerated
`@workpulse/api-client`; T7 lands the e2e flow and records the decisions.
Every task ends with `make check` green. Repo patterns this plan follows:
services take `now: datetime` as the last parameter (router `_now()` is the only
wall-clock read), errors are `WorkPulseError` subclasses with pinned codes,
paged lists are `{"items", "page"}` and unpaged lists are bare arrays
(`EmploymentList` style), domain repositories are Protocols + tenant-scoped
Postgres impls, routers do `identity → policy → service`.

### Acceptance criteria → where they are satisfied

| # | Criterion (restated) | Changes that satisfy it | Proof |
| --- | --- | --- | --- |
| AC1 | check-in → `201` open record with the correct workday for the location timezone | T4 `AttendanceService.check_in` computes `workday` = date of `checked_in_at` in the employee's location tz (`locations.timezone`, zoneinfo); T5 route `checkIn` | unit `test_check_in.py` (23:58 check-in) + `tests/integration/test_attendance_api.py` (pinned body, Ho Chi Minh employee) |
| AC2 | check-out closes the record and reports `duration_minutes` | T4 `check_out` sets `checked_out_at=now` and floor-minutes `duration_minutes`; T1 pins the field on `AttendanceRecord` | unit `test_check_out.py` + integration `200` body |
| AC3 | check-in after `shift.start + grace` → `late: true` + an `attendance.late` contract payload | T4 lateness judgement (location tz, exclusive boundary) + T2 `attendance_late` builder | unit boundary test (exactly at `start+grace` → not late) + `tests/contract/test_attendance_events.py` |
| AC4 | double check-in → `409 attendance_already_checked_in` | T3 partial unique index on open records (concurrency backstop) + T4 pre-check + repo IntegrityError mapping | unit + `test_attendance_failures.py` |
| AC5 | check-out without an open record → `409 attendance_not_open` | T4 `check_out` validation | unit + `test_attendance_failures.py` |
| AC6 | check-in by a terminated employee → `409 employee_not_active` | T4 active-status check (check-in and check-out) | unit + `test_attendance_failures.py` |
| AC7 | tenant B's board shows none of tenant A's records | T3 tenant-scoped repositories; T4 board assembly scoped to visible employees | `tests/integration/test_attendance_tenant_isolation.py` |
| AC8 | `Employee`-role request for another employee's records → `403`/`404` (pinned: `404 employee_not_found`) | T4 visibility scope in the service + `policies.py`; T5 routes | `test_attendance_roles.py` |
| AC9 | unit tests use an injected clock — no wall-time assertions | T4 unit tests pass a `NOW` constant to every call (employees pattern) | `make check-api` unit suite |
| AC10 | the three event schemas exist and validate produced payloads | T2 contracts + `domains/attendance/events.py` pure builders + contract test | `test_attendance_events.py` |
| AC11 | each check-in/check-out wrote an audit entry | T4 audit writes (`attendance.checked_in` / `attendance.checked_out`; note values never in `changes`) | `test_attendance_audit.py` |
| AC12 | web: check in → board counts and activity feed update on refresh; a `409` renders as a visible error state | T6 board page + check-in/out action + `ApiError.code` → visible error mapping; T7 e2e | `apps/web/tests/attendance/*` (gate `make check-web`) · `tests/e2e/attendance.spec.ts` (gate `make check-e2e`) |

### T1 — Pin the API contract (docs only)

Rewrite `## API contract (proposed)` as final — drop "proposed" — and pin these
deltas (they are contract surface; review here, not during the build):

- **Shapes** (schemas titled exactly these names):

  ```jsonc
  AttendanceRecord {
    "id", "tenant_id", "employee_id", "workday": date,
    "checked_in_at": date-time, "checked_out_at": date-time|null,
    "work_mode": "onsite|remote",
    "late": bool, "shift_id": uuid|null,
    "check_in_note": string|null, "check_out_note": string|null, // replaces the single "note"
    "duration_minutes": int|null,   // null while open; floor whole minutes at close
    "created_at": date-time
  }
  Board {
    "date": date,
    "counts": { "present", "remote", "late", "on_leave", "absent", "checked_out" },
    "activity": [ { "occurred_at": date-time, "employee_id": uuid,
                    "action": "checked_in|checked_out" } ]
  }
  Shift { "id", "tenant_id", "name", "start_time": "HH:MM", "end_time": "HH:MM",
          "grace_minutes": int, "location_id": uuid|null,
          "created_at", "updated_at" }
  CheckInRequest  { "work_mode": string, "check_in_note"? }  // 1..500 chars
  CheckOutRequest { "check_out_note"? }
  ShiftCreate/ShiftUpdate { "name", "start_time", "end_time",
          "grace_minutes" >= 0, "location_id"? }
  ```

  Note fields are free text and treated like PII: never in logs, event payloads
  or audit `changes` values (`changes` says `"changed"`).
- **Routes & operationIds** (7 — drive orval hook names): `checkIn`
  (`POST /attendance/check-ins` → `201 AttendanceRecord`) · `checkOut`
  (`POST /attendance/records/{id}/check-out` → `200 AttendanceRecord`) ·
  `listAttendanceRecords` (`GET /attendance/records` → `{"items", "page"}`) ·
  `getAttendanceBoard` (`GET /attendance/board`) · `listShifts`
  (`GET /shifts` → `Shift[]` bare array, unpaged like `EmploymentList`, ordered
  `location_id` nulls-last then `name`) · `createShift` (`POST /shifts` → 201) ·
  `updateShift` (`PATCH /shifts/{id}` → 200). No DELETE: shifts are adjusted,
  never removed in Phase 1 (removal semantics wait for scheduling).
- **`listAttendanceRecords` filters**: `employee_id`, `from`, `to` (both filter
  `workday`, inclusive), `late`, `page`, `page_size` (existing `PageParams`
  clamping). Order `checked_in_at` desc, `id` desc (stable pagination).
- **Check-in semantics** (Decision #3 made concrete): server time only — no
  timestamp field is accepted anywhere (any extra field is rejected by
  `additionalProperties: false`). The actor's employee resolves from
  `X-Actor-Id` (like `/employees/me`); an actor with no employee record →
  `404 employee_not_found`. `work_mode` is a plain string validated in the
  service → `400 invalid_work_mode` (an enum in the schema would collapse the
  pinned code into the generic 422).
- **Workday & lateness**: `workday` = calendar date of `checked_in_at` in the
  **employee's location timezone** (`employees.location_id → locations.timezone`,
  `zoneinfo`). A record keeps its workday across midnight (23:58 → 00:10).
  Shift resolution: the employee's location's shift, else the tenant default
  (`shift.location_id IS NULL`), else none → `late` stays `false`. Lateness:
  convert `checked_in_at` to the location tz, build `shift.start_time` on the
  record's `workday` in that tz, and set
  `late = checked_in_at > shift_start + grace_minutes` — the boundary is
  **exclusive** (exactly at `start + grace` is not late). `minutes_late` (event
  payload) = floor whole minutes past `start + grace`. The `late` flag is set
  once at check-in and never cleared.
- **Shift scoping** (new decision — see Risks): **at most one shift per scope
  per tenant** — one per `location_id`, plus at most one tenant default
  (`location_id` null). Enforced by unique indexes; a second shift for the same
  scope → `409 shift_scope_taken`. `start_time`/`end_time` are `"HH:MM"`
  strings (`^([01]\d|2[0-3]):[0-5]\d$`); `end_time > start_time` else `422
  shift_time_invalid`. A shift may reference an archived location (lateness
  still works; ADR 0007 does not apply — no archive on shifts in Phase 1).
- **Board semantics** — counts are independent, **not** a partition (a person
  can be in `present` and `late` at once; UI copy says so):
  - `present` — open record for the date, `work_mode=onsite`
  - `remote` — open record for the date, `work_mode=remote`
  - `late` — any record for the date with `late=true` (open or closed)
  - `checked_out` — any record for the date with `checked_out_at` set
  - `on_leave` — always `0` in Phase 1 (field stays so the contract never changes)
  - `absent` — active employee in scope with an expected shift (location's,
    else tenant default) **on the requested date**, with **no record at all**
    for that date, and `now > shift.start_time + grace` for that date in the
    location tz. A late check-in removes the employee from `absent` into
    `present`/`remote` + `late`. The accepted over-count on rest days
    (Decision #7) is documented in UI copy.
  - `activity` — check-in and check-out entries of records with
    `workday == date` (`occurred_at` = `checked_in_at` / `checked_out_at`),
    newest first, capped at 20.
  - `?date=YYYY-MM-DD` defaults to the server's current date in **UTC** — there
    is no tenant-level timezone in the system (see Risks). The web board always
    sends an explicit date.
- **Roles** (the six repo roles; Decision #8, ADR 0004):
  - check-in / check-out: self only (any role); check-out of a record that is
    not the actor's → `404 attendance_record_not_found`.
  - `GET /attendance/records`: `employee`/`finance` → own records only
    (server-scoped to self; an `employee_id` filter naming anyone else →
    `404 employee_not_found`); `manager` → self + direct reports (same rule for
    out-of-scope filters); `hr`/`admin`/`super_admin` → all.
  - `GET /attendance/board`: `manager` (self + reports scope), `hr`, `admin`,
    `super_admin`; `employee`/`finance` → `403 forbidden`.
  - `/shifts` (all three routes): `hr`/`admin`/`super_admin`; else `403`.
  - Cross-tenant anything → `404`/empty, never another tenant's data.
- **Error code → status** (uniform `{"error": {"code", "message"}}`, static
  messages, values never echoed):
  `attendance_already_checked_in` 409 · `attendance_not_open` 409 ·
  `attendance_record_not_found` 404 · `employee_not_active` 409 ·
  `shift_not_found` 404 · `invalid_work_mode` 400 · `shift_time_invalid` 422 ·
  `shift_scope_taken` 409 · `employee_not_found` 404 (reused) · `forbidden` 403.

Verify: docs-only diff; `make check` still green.

### T2 — Event contracts + Phase-1 payload builders

Contracts before any producer/consumer (rule 4; file names match the pinned
example tree in `contracts/events/README.md`):

- `contracts/events/attendance/attendance-checked-in.v1.json` — `data`:
  `attendance_id`, `employee_id`, `workday`, `work_mode`, `late`.
- `contracts/events/attendance/attendance-checked-out.v1.json` — `data`:
  `attendance_id`, `employee_id`, `workday`, `duration_minutes`.
- `contracts/events/attendance/attendance-late.v1.json` — `data`:
  `attendance_id`, `employee_id`, `workday`, `shift_id`, `minutes_late`.

All three: JSON Schema draft 2020-12, `additionalProperties: false`,
identifiers/derived scalars only — no notes, names, or times beyond the listed
fields.

- `apps/api/app/domains/attendance/events.py` — pure builders
  (`attendance_checked_in/checked_out/late`) returning full envelope-shaped
  dicts via the `_envelope` pattern of `domains/employees/events.py`
  (`event_id` from `uuid7`, `version: 1`). This is the exact payload source
  Phase 3's publisher will call; **no publishing in Phase 1**.
- `apps/api/tests/contract/test_attendance_events.py` — validates one payload
  per builder against its `data` schema and the whole event against
  `common/event-envelope.v1.json`; asserts no PII-shaped keys
  (`name`, `email`, `phone`, `salary`, `reason`, **`note`**) in any payload.

Verify: `make check-api` (contract tests pass; nothing else touched).

### T3 — Persistence: models · migration 0003 · repositories · seeds

- `domains/attendance/models.py` — `Shift` and `AttendanceRecord` tables
  following `domains/employees/models.py` conventions (uuid7 `id`, non-null
  `tenant_id` column, `created_at`/`updated_at` timestamptz):
  - `shifts`: `name`, `start_time`/`end_time` (`Time`), `grace_minutes` (int),
    `location_id` (nullable, same FK style as existing models). Unique partial
    indexes `(tenant_id, location_id) WHERE location_id IS NOT NULL` and
    `(tenant_id) WHERE location_id IS NULL` → the `shift_scope_taken` backstop.
  - `attendance_records`: `employee_id` FK `employees.id`, `workday` (Date),
    `checked_in_at`/`checked_out_at` (timestamptz), `work_mode` (str), `late`
    (bool), `shift_id` (nullable FK `shifts.id`), `check_in_note`/
    `check_out_note` (str nullable). **Partial unique index**
    `(tenant_id, employee_id) WHERE checked_out_at IS NULL` — the concurrent
    double-check-in guarantee (at most one open record). Plus indexes on
    `(tenant_id, workday)` and `(tenant_id, employee_id, workday)`.
- `apps/api/alembic/versions/0003_attendance.py` — `revision="0003"`,
  `down_revision="0002"`; creates both tables + indexes. Never edit 0001/0002.
- `apps/api/app/domains/attendance/repository.py` — Protocols
  (`ShiftRepository`, `AttendanceRepository`, filters dataclass) mirroring
  `domains/employees/repository.py`;
  `apps/api/app/infrastructure/postgres/repositories/attendance.py` —
  `PostgresShiftRepository`, `PostgresAttendanceRepository`. All methods take
  `tenant_id` first; unique-violation mapping via the `_map_unique_violation`
  pattern (open-record and shift-scope indexes → domain errors). Employee +
  location timezone lookups join existing tables (check-in and board need the
  employee's location tz).
- `database/postgres/seeds/002_attendance.sql` — idempotent
  (`ON CONFLICT DO NOTHING`, fixed UUIDs, dev tenant
  `00000000-0000-4000-8000-000000000001`): one tenant-default shift
  (09:00–18:00, grace 10) and one `Ho Chi Minh Office`-scoped shift
  (08:30–17:30, grace 10) so location-scoped lateness is demonstrable. No
  employee rows — e2e and manual testing create their own.
- `apps/api/tests/integration/conftest.py` — add `attendance_records` and
  `shifts` to the `ALL_TABLES` truncation list.

Verify: `make check-api` green (existing suites unaffected; migration applies
on the throwaway test DB).

### T4 — Domain: exceptions · policies · service · unit tests

- `domains/attendance/exceptions.py` — one subclass per pinned code (mirror
  `employees/exceptions.py`): `AttendanceAlreadyCheckedInError` 409,
  `AttendanceNotOpenError` 409, `AttendanceRecordNotFoundError` 404,
  `EmployeeNotActiveError` 409, `ShiftNotFoundError` 404,
  `InvalidWorkModeError` 400, `ShiftTimeInvalidError` 422,
  `ShiftScopeTakenError` 409.
- `domains/attendance/policies.py` — `ensure_can_read_records`,
  `ensure_can_read_board` (manager/hr/admin/super_admin),
  `ensure_can_manage_shifts` (hr/admin/super_admin); check-in/out is self-only
  and resolved from the identity in the service. Routers call guards before the
  service (existing pattern).
- `domains/attendance/service.py` — `AttendanceService` + `ShiftService`
  (constructor-injected repositories + audit port + an employees/location lookup
  port, mirroring `EmployeeService`'s port injection):
  - `check_in(identity, payload, now)` — resolve actor's employee (`404
    employee_not_found` if none), active check (`409 employee_not_active`),
    open-record pre-check (`409 attendance_already_checked_in`), validate
    `work_mode`, resolve shift, compute `workday` + `late` + `minutes_late` in
    the location tz, insert (IntegrityError on the partial unique index →
    `409 attendance_already_checked_in`, the two-tab case), audit
    (`attendance.checked_in`; `changes` ids/flags only), build
    `attendance.checked_in` (and `attendance.late` when late) payloads via T2.
  - `check_out(identity, record_id, payload, now)` — record exists in tenant and
    is the actor's (else `404 attendance_record_not_found`), open (else `409
    attendance_not_open`), actor active (else `409 employee_not_active`); sets
    `checked_out_at=now`, `duration_minutes` = floor minutes; audit +
    `attendance.checked_out` payload. `workday` is never recomputed.
  - `list_records(...)` — filters per T1; visibility scope per T1.
  - `board(identity, date, now)` — counts per T1 over the actor's visible
    employees; `absent` needs each employee's location tz + resolved shift.
  - `ShiftService.list/create/update` — scope uniqueness (`409
    shift_scope_taken`), window validation (`422 shift_time_invalid`),
    `404 shift_not_found` on unknown/cross-tenant ids; audit
    `shift.created`/`shift.updated` (entity_type `shift`).
- `domains/attendance/schemas.py` — wire models per T1 (`AttendanceRecord`,
  `Board`, `Shift`, request bodies, `AttendanceRecordListPage`, `ShiftList`).
- `apps/api/tests/unit/attendance/` — `conftest.py` + `fakes.py` (in-memory
  repositories; `NOW` constant, **never wall time**) +:
  - `test_check_in.py` — happy path; **23:58 check-in workday** in
    Asia/Ho_Chi_Minh; exactly `start+grace` → `late=False`; past the boundary →
    `late=True` + `minutes_late`; no shift → `late=False`; double check-in →
    `409 attendance_already_checked_in`; terminated → `409 employee_not_active`;
    invalid work mode → `400 invalid_work_mode`.
  - `test_check_out.py` — closes + `duration_minutes`; 23:58 → 00:10 keeps the
    check-in workday; no open record → `409 attendance_not_open`; someone
    else's record → `404`; terminated actor → `409 employee_not_active`.
  - `test_board.py` — count semantics (overlapping `present`/`late`;
    `absent` only after `start+grace` and only with zero records; `on_leave`
    is 0; activity newest-first + cap 20).
  - `test_policies.py` — denied cases per the T1 role matrix.
  - `test_shifts.py` — scope conflict `409`, `end <= start` → `422`.

Verify: `make check-api` green.

### T5 — Routers · integration/audit/role tests · regenerate clients

- `apps/api/app/api/v1/attendance.py` — 7 routes per T1 with the pinned
  operationIds; `identity → policy → service`; module-level `_now()` as the
  only wall-clock read (check-in, check-out **and** board take `now`). Register
  in `api/v1/__init__.py` (attendance routes tagged `attendance`, shifts
  routes tagged `shifts`; declare `/records/{id}/check-out` so it cannot
  collide with list routes).
- Integration tests (existing helpers `create_org_refs`, `employee_payload`,
  `identity_headers(TENANT_A/B, ACTOR, role)` reused):
  - `test_attendance_api.py` — check-in `201` pinned body + correct workday for
    a Ho Chi Minh employee; check-out `200` with `duration_minutes`; records
    list filters + pagination; board counts/activity update after check-in and
    check-out; shifts CRUD happy path.
  - `test_attendance_failures.py` — the three 409s (double check-in, check-out
    not open, terminated check-in), `invalid_work_mode` 400,
    `shift_scope_taken` 409, `shift_time_invalid` 422, check-out of another's
    record `404`.
  - `test_attendance_tenant_isolation.py` — tenant B's board/records/check-out
    against tenant A's data → empty / `404`, never A's rows (AC7).
  - `test_attendance_roles.py` — `employee`/`finance` board → `403`;
    `employee` list with another's `employee_id` → `404 employee_not_found`
    (AC8); `manager` list scoped to reports; `employee` create shift → `403`.
  - `test_attendance_audit.py` — one entry per check-in/check-out
    (`attendance.checked_in` / `attendance.checked_out`); `changes` never
    contains note values.
- `make gen-api` — re-exports `contracts/openapi/openapi.json` (keeps
  `test_openapi_drift.py` green) and regenerates `@workpulse/api-client`
  (hooks `useCheckIn`, `useCheckOut`, `useListAttendanceRecords`,
  `useGetAttendanceBoard`, `useListShifts`, `useCreateShift`,
  `useUpdateShift`). Commit the regenerated output with this task.

Verify: `make check` green (api + web both consume the regenerated client).

### T6 — Web: check-in/out action + live workforce board

- `apps/web/features/attendance/` mirroring `features/employees/`:
  - `api.ts` — attendance query-key helpers + invalidation (board + records
    invalidated after check-in/out).
  - `hooks/use-attendance.ts` — `useAttendanceBoard`, `useAttendanceRecords`,
    `useCheckIn`, `useCheckOut`; `errorMessage()` + code mapping
    (`attendance_already_checked_in`, `attendance_not_open`,
    `employee_not_active`, `forbidden`, …) to visible static messages.
  - `components/` — `CheckInPanel` (onsite/remote toggle + optional note; shows
    the open state and the Check out action), `BoardCountCards` (six counts;
    `on_leave` labelled "always 0 until Leave lands"), `ActivityFeed`,
    `AttendanceRecordsTable` (records list with date-range filter),
    `labels.ts`/`dates.ts` (dayjs; board "today" computed client-side and sent
    explicitly — T1).
  - UI copy states the `absent` over-count limitation (Decision #7) and that
    late employees also appear in `present`/`remote` (T1 count semantics).
- `apps/web/app/(dashboard)/attendance/page.tsx` — board page (check-in action
  on top, counts + activity, records table); nav entry in the dashboard layout.
- Web tests `apps/web/tests/attendance/` (Vitest + Testing Library, gate
  `make check-web`): board renders counts + activity; check-in success
  refreshes; **a `409 attendance_already_checked_in` renders as the visible
  error state** (AC12 failure case).

Verify: `make check` green.

### T7 — E2E · records

- `tests/e2e/attendance.spec.ts` (gate `make check-e2e`, pattern of
  `employees.spec.ts` — `RUN`-suffixed fixtures; dev-identity headers are
  injected by the web transport): fixture creates an employee via the API
  against the seeded org refs (fixed seed UUIDs), then drives the UI: check in →
  board counts and activity update → check out → `checked_out` count; a second
  check-in attempt shows the visible `409` error state. Repeatable across runs
  (fresh employee per run).
- Records (same change): tick and move the `attendance.md` item in
  `tasks/TODO.md`; write **ADR 0009 — Attendance Phase-1 semantics** (shift
  scoping, exclusive lateness boundary, overlapping board counts, UTC default
  board date) under `.ai/decisions/`; set this spec's status to `complete` with
  the acceptance criteria ticked.

Verify: `make check` green; `make check-e2e` green.

### Risks, unknowns & decisions that deserve an ADR

1. **No tenant-level timezone exists** — the spec's "default: today, tenant tz"
   had nowhere to read from (there is no `tenants` table). Pinned: default
   board date = server UTC date; the web always sends an explicit date. A
   per-tenant default timezone is a Phase 2 settings candidate with its own ADR.
2. **Shift scoping is a simplification** — one shift per location scope. Real
   rosters (multiple shifts per site, per-employee assignment) are Phase 2–3
   scheduling. The unique-index choice is cheap to relax; the lateness
   resolution rule is what gets recorded in ADR 0009.
3. **Board counts overlap** (not a partition) — the least surprising reading of
   the spec (the `late` flag is orthogonal to presence; `remote` is a work
   mode), but it is a contract decision: pinned in T1, recorded in ADR 0009. If
   the PO wants mutually exclusive buckets, that is a T1 change — cheap now,
   expensive once the web lands.
4. **Terminated employee with an open record** — check-out is also blocked
   (`409 employee_not_active`, per the business rule), so a record open at
   termination stays open and keeps counting as present/remote. Accepted for
   Phase 1; Phase 3 correction requests (or an auto-close hook on terminate)
   resolve it.
5. **Concurrent double check-in** relies on the partial unique index + repo
   IntegrityError mapping as the production backstop; tests cover the pre-check
   and the mapping path (a true multi-connection race is out of unit scope).
6. **`absent` over-counts rest days** (Decision #7) and cannot subtract leave
   until Phase 3 — accepted PO limitation; the board UI must say so.
7. **Board query cost** grows with active-employee count (per-request timezone
   math). Fine for Phase 1; Phase 2's hot-state PresenceState (Redis) is the
   designed answer — do not pre-optimize.
8. Note fields are free text and treated as PII in logs/audit/events (the
   contract-test key whitelist includes `note`).

### Test strategy

| Gate | Tests added | Failure cases covered |
| --- | --- | --- |
| `make check-api` — unit `tests/unit/attendance/` | check-in, check-out, board, shifts, policies — all with injected `NOW` | double check-in 409 · check-out not open 409 · terminated 409 · invalid work mode 400 · lateness boundary (exactly `start+grace` not late) · no-shift never late · denied roles 403 |
| `make check-api` — integration `tests/integration/` | `test_attendance_api.py`, `test_attendance_failures.py`, `test_attendance_tenant_isolation.py`, `test_attendance_roles.py`, `test_attendance_audit.py` | the three 409s over HTTP · cross-tenant board/list/check-out isolation (404/empty) · employee reading another's records 404 · shift scope conflict 409 · audit entries written with no note values |
| `make check-api` — contract `tests/contract/` | `test_attendance_events.py`; `test_openapi_drift.py` refreshed via `make gen-api` | event payload violates schema · PII-shaped key (`note` included) in a payload |
| `make check-web` | `apps/web/tests/attendance/` (board rendering, check-in flow, error states) | `409` renders as a visible error state (AC12) · board shows counts + activity after refresh |
| `make check-e2e` | `tests/e2e/attendance.spec.ts` | full check-in → board update → check-out flow · repeated check-in shows the error state |
| Not covered in Phase 1 | true parallel-request race (index backstop only) · WebSocket push (Phase 2) · absent-on-rest-day accuracy (needs rosters/Leave) | — |

Rules honored: no wall-time assertions (clock injected as `now`), a
tenant-isolation test per domain, denied-role + audit tests for every mutating
flow, deterministic fixtures (fixed seed UUIDs, `RUN`-suffixed e2e data), and
happy path + failure case on every surface touched.