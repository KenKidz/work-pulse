# Feature: Attendance

- **Status:** draft
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

_Not planned yet._