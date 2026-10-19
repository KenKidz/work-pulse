"""Use cases for the Attendance domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/attendance.md`):

- **Server time is authoritative**: every mutation takes `now` (router-injected)
  and this module never reads the wall clock. Client-supplied timestamps do not
  exist in the schemas.
- **Workday**: the calendar date of `checked_in_at` in the employee's location
  timezone (`employees.location_id → locations.timezone`, `zoneinfo`). A record
  keeps its workday across midnight (23:58 → 00:10 stays one workday).
- **Lateness**: shift resolution is the employee's location's shift, else the
  tenant default (`shift.location_id IS NULL`), else none → never late. `late`
  is judged once at check-in in the location timezone against
  `shift.start_time + grace_minutes`; the boundary is **exclusive** (exactly at
  the boundary is not late) and the flag is never cleared. `minutes_late` is
  floor whole minutes past the boundary.
- **At most one open record** per employee: a pre-check here plus the partial
  unique index backstop (Postgres maps that race to
  `409 attendance_already_checked_in`).
- **Self-only capture**: check-in/check-out resolve the employee linked to the
  acting user (`identity.employee_id`); a non-active employee cannot check in
  or out, and a workday covered by APPROVED leave refuses check-in
  (`409 employee_on_leave`) while check-out stays allowed (ADR 0014).
- **Visibility** (T1 role matrix): `employee`/`finance` see their own records;
  `manager` sees self + direct reports; `hr`/`admin`/`super_admin` see all. An
  out-of-scope `employee_id` filter is a `404 employee_not_found` — existence
  is not leaked.
- **Board counts are independent, not a partition** (a person may be `present`
  and `late`): each count is computed over each employee's **current** (newest)
  record for the date — a re-check-in replaces its earlier record's
  contribution instead of counting twice, so counts and the presence rows
  always agree. `absent` needs an expected shift, zero records for the date and
  `now` past `start + grace` in the employee's location timezone; an employee
  covered by APPROVED leave is `on_leave` — one row, record-based counts and
  `absent` excluded (ADR 0014).
- **Shift scopes**: at most one shift per scope (shared indexes/checks in the
  repository) and a client-supplied `location_id` must exist in the actor's
  tenant → `404 location_not_found`; archived locations stay referenceable.
- **Audit**: every check-in/check-out writes an entry with ids/flags only —
  note values are PII and never reach `changes`, logs or events.
- **Events**: mutations build their v1 payloads and append them to the
  transactional outbox in the same transaction (Phase 3, ADR 0012) — a
  rolled-back capture leaves no event row — and still return them on
  `AttendanceMutation`. Nothing is published from here; the relay moves
  committed rows to Kafka at-least-once.
- **Presence projection**: `board_detail` is the one board code path — counts,
  per-employee presence rows and the activity feed — behind both
  `GET /attendance/board` and the Phase-2 realtime snapshot, so the two can
  never disagree (`.ai/features/attendance.md` + realtime spec, T3). The
  write path is bus-free: the wall's only live input is the realtime consumer
  projecting these events off `workpulse.events` (ADR 0011/0013), so a Redis
  or broker outage never touches a capture.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from uuid_utils.compat import uuid7

from app.domains.attendance.events import (
    attendance_checked_in,
    attendance_checked_out,
    attendance_late,
)
from app.domains.attendance.exceptions import (
    AttendanceAlreadyCheckedInError,
    AttendanceNotOpenError,
    AttendanceRecordNotFoundError,
    EmployeeNotActiveError,
    EmployeeOnLeaveError,
    InvalidWorkModeError,
    ShiftNotFoundError,
    ShiftScopeTakenError,
    ShiftTimeInvalidError,
)
from app.domains.attendance.repository import (
    AttendanceFilters,
    AttendanceRepository,
    EmployeeContext,
    ShiftRepository,
)
from app.domains.attendance.schemas import (
    AttendanceRecord,
    AttendanceRecordListPage,
    Board,
    BoardAction,
    BoardActivity,
    BoardCounts,
    BoardPresenceRow,
    CheckInRequest,
    CheckOutRequest,
    Shift,
    ShiftCreate,
    ShiftList,
    ShiftUpdate,
    WorkMode,
)
from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.employees.schemas import EmployeeStatus
from app.domains.organizations.service import LocationService
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity, Role
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

ACTIVITY_LIMIT = 20  # board feed cap (pinned)


def _ref(value: UUID | None) -> str | None:
    """Audit-friendly id value (never PII)."""
    return str(value) if value is not None else None


def _jsonable(value: Any) -> Any:
    """Make audit `changes` values JSON-safe (ids/state only — non-PII)."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return value


def _expected_shift(shifts: Sequence[Shift], location_id: UUID) -> Shift | None:
    """Pinned resolution: the location's shift, else the tenant default, else none.

    One shared implementation so check-in lateness and the board's `absent`
    count can never disagree about which shift applies.
    """
    default: Shift | None = None
    for shift in shifts:
        if shift.location_id is None:
            default = shift
        elif shift.location_id == location_id:
            return shift
    return default


def _shift_boundary(shift: Shift, workday: date, timezone: ZoneInfo) -> datetime:
    """`shift.start_time + grace_minutes` on `workday` in the location tz."""
    start = datetime.combine(workday, shift.start_time, tzinfo=timezone)
    return start + timedelta(minutes=shift.grace_minutes)


def _presence_status(
    record: AttendanceRecord,
) -> Literal["present", "remote", "checked_out"]:
    """Open/closed + work mode → the live status (never `absent`/`on_leave`)."""
    if record.checked_out_at is not None:
        return "checked_out"
    if record.work_mode == WorkMode.REMOTE:
        return "remote"
    return "present"


def _minutes_late(
    record: AttendanceRecord, shift: Shift | None, timezone: ZoneInfo
) -> int | None:
    """Whole minutes past `shift.start + grace`, recomputed at read time.

    `None` when the record is not `late` or its shift no longer resolves.
    Recomputing (instead of storing) means an edit to the shift changes the
    displayed minutes after a late check-in — accepted drift; the `late` flag
    itself never changes (realtime spec, risk 3). Clamped at 0 so the wire
    contract's `minimum: 0` always holds.
    """
    if not record.late or shift is None:
        return None
    boundary = _shift_boundary(shift, record.workday, timezone)
    return max(0, int((record.checked_in_at - boundary).total_seconds() // 60))


def _presence_row(
    record: AttendanceRecord, shift: Shift | None, timezone: ZoneInfo
) -> BoardPresenceRow:
    """One employee's presence row from their (current) workday record.

    The single row projection behind the REST board snapshot and the realtime
    frames: `since` is `checked_in_at` while open and `checked_out_at` once
    closed; `minutes_late` is recomputed from the record's shift at read time.
    """
    return BoardPresenceRow(
        employee_id=record.employee_id,
        status=_presence_status(record),
        late=record.late,
        minutes_late=_minutes_late(record, shift, timezone),
        since=(
            record.checked_out_at
            if record.checked_out_at is not None
            else record.checked_in_at
        ),
        attendance_id=record.id,
    )


def _absent_rows(
    visible: Sequence[EmployeeContext],
    records: Sequence[AttendanceRecord],
    shifts: Sequence[Shift],
    board_date: date,
    now: datetime,
    covered: set[UUID],
) -> list[BoardPresenceRow]:
    """Active employees past `shift.start + grace` with zero records.

    Shifts resolve as the employee's location's shift, else the tenant
    default, else none (never absent). Employees covered by APPROVED leave
    are never absent — they render `on_leave` instead (ADR 0014). Identifiers
    and state only.
    """
    recorded = {record.employee_id for record in records}
    rows: list[BoardPresenceRow] = []
    for context in visible:
        if (
            context.status != EmployeeStatus.ACTIVE
            or context.id in recorded
            or context.id in covered
        ):
            continue
        shift = _expected_shift(shifts, context.location_id)
        if shift is None:
            continue
        boundary = _shift_boundary(shift, board_date, ZoneInfo(context.timezone))
        if now > boundary:
            rows.append(
                BoardPresenceRow(
                    employee_id=context.id,
                    status="absent",
                    late=False,
                    minutes_late=None,
                    since=None,
                    attendance_id=None,
                )
            )
    return rows


@dataclass(frozen=True, slots=True)
class AttendanceMutation:
    """A completed check-in/check-out plus the event payloads it produced.

    The service has already appended these envelopes to the transactional
    outbox in the same transaction (Phase 3, ADR 0012); returning them keeps
    the produced facts observable and the router-facing shape unchanged.
    """

    record: AttendanceRecord
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class BoardDetail:
    """The full board projection: counts + presence rows + activity feed.

    One code path behind both `GET /attendance/board` (which maps to the wire
    `Board`) and the realtime snapshot (which maps to its wire schema), so the
    surfaces can never drift about workday, lateness or `absent` (ADR 0009).
    `rows` holds one row per employee — the newest workday record wins — plus
    one `absent` row per currently-absent employee; `activity` is the shared
    transition feed (newest first, capped).
    """

    date: date
    counts: BoardCounts
    rows: list[BoardPresenceRow]
    activity: list[BoardActivity]


class AttendanceService:
    def __init__(
        self,
        repo: AttendanceRepository,
        shifts: ShiftRepository,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._shifts = shifts
        self._audit = audit
        self._outbox = outbox

    # --- captures ------------------------------------------------------------

    async def check_in(
        self, identity: Identity, payload: CheckInRequest, now: datetime
    ) -> AttendanceMutation:
        """Open a record for the linked employee — self-only, server time only."""
        tenant_id = identity.tenant_id
        if identity.employee_id is None:
            raise EmployeeNotFoundError()
        context = await self._repo.employee_context(tenant_id, identity.employee_id)
        if context is None:
            raise EmployeeNotFoundError()
        if context.status != EmployeeStatus.ACTIVE:
            raise EmployeeNotActiveError()
        location_timezone = ZoneInfo(context.timezone)
        workday = now.astimezone(location_timezone).date()
        # Approved leave covering the workday blocks the capture, symmetric
        # with `employee_not_active`: `ON_LEAVE` and `PRESENT` are
        # contradictory presence states (leave spec decision 9, ADR 0014).
        # Check-out stays allowed — closing an open record is always safe.
        if await self._repo.covers_leave(tenant_id, context.id, workday):
            raise EmployeeOnLeaveError()
        if await self._repo.open_for_employee(tenant_id, context.id) is not None:
            raise AttendanceAlreadyCheckedInError()
        try:
            work_mode = WorkMode(payload.work_mode)
        except ValueError:
            raise InvalidWorkModeError() from None

        shift = await self._resolve_shift(tenant_id, context.location_id)
        late = False
        if shift is not None:
            late = now > _shift_boundary(shift, workday, location_timezone)

        record = await self._repo.add(
            tenant_id,
            uuid7(),
            employee_id=context.id,
            workday=workday,
            checked_in_at=now,
            work_mode=work_mode,
            late=late,
            shift_id=shift.id if shift is not None else None,
            check_in_note=payload.check_in_note,
        )

        # The one minutes-late rule (`_minutes_late`) on the persisted record,
        # so the event payloads and the board rows share the math. `0` stands
        # in for "not late"; the late event is only emitted when `late` is
        # true, and then the value is the computed minutes. The checked-in
        # payload nulls it when not late (ADR 0013's wire rule).
        minutes_late = _minutes_late(record, shift, location_timezone) or 0

        produced: list[dict[str, Any]] = [
            attendance_checked_in(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                attendance_id=record.id,
                employee_id=record.employee_id,
                workday=record.workday,
                work_mode=work_mode.value,
                late=late,
                # Set with `late` at check-in; `null` otherwise (wire rule).
                minutes_late=minutes_late if late else None,
            )
        ]
        if late and shift is not None:
            produced.append(
                attendance_late(
                    tenant_id=tenant_id,
                    actor_id=identity.actor_id,
                    occurred_at=now,
                    attendance_id=record.id,
                    employee_id=record.employee_id,
                    workday=record.workday,
                    shift_id=shift.id,
                    minutes_late=minutes_late,
                )
            )

        # One transaction (ADR 0012): the record, its audit entry and these
        # envelopes commit together — a rolled-back capture leaves no event row.
        for envelope in produced:
            await self._outbox.append(envelope)

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="attendance.checked_in",
                entity_type="attendance",
                entity_id=record.id,
                occurred_at=now,
                # Ids and flags only; note fields never appear in `changes`.
                changes={
                    "employee_id": {"old": None, "new": str(record.employee_id)},
                    "workday": {"old": None, "new": record.workday.isoformat()},
                    "work_mode": {"old": None, "new": work_mode.value},
                    "late": {"old": None, "new": late},
                    "shift_id": {"old": None, "new": _ref(record.shift_id)},
                },
            )
        )
        return AttendanceMutation(record=record, events=tuple(produced))

    async def check_out(
        self,
        identity: Identity,
        record_id: UUID,
        payload: CheckOutRequest,
        now: datetime,
    ) -> AttendanceMutation:
        """Close the linked employee's open record; the workday is never recomputed."""
        tenant_id = identity.tenant_id
        record = await self._repo.get(tenant_id, record_id)
        if (
            identity.employee_id is None
            or record is None
            or record.employee_id != identity.employee_id
        ):
            # Someone else's record (or an unlinked user's) is a 404 — its
            # existence is not leaked.
            raise AttendanceRecordNotFoundError()
        if record.checked_out_at is not None:
            raise AttendanceNotOpenError()

        context = await self._repo.employee_context(tenant_id, identity.employee_id)
        if context is None:  # pragma: no cover — the record proves the employee
            raise EmployeeNotFoundError()
        if context.status != EmployeeStatus.ACTIVE:
            raise EmployeeNotActiveError()

        duration_minutes = int((now - record.checked_in_at).total_seconds() // 60)
        closed = await self._repo.close(
            tenant_id,
            record_id,
            checked_out_at=now,
            duration_minutes=duration_minutes,
            check_out_note=payload.check_out_note,
        )
        if closed is None:
            # A concurrent check-out closed it under us — never overwrite.
            raise AttendanceNotOpenError()

        # Recomputing `minutes_late` at emit time from the record's shift keeps
        # a late check-in's minutes on the checked-out payload (ADR 0013). The
        # shift is only resolved when it can matter (not-late records never
        # carry minutes), saving a SELECT on the common capture path.
        shift = (
            await self._shifts.get(tenant_id, closed.shift_id)
            if closed.late and closed.shift_id is not None
            else None
        )
        minutes_late = _minutes_late(closed, shift, ZoneInfo(context.timezone))

        produced: list[dict[str, Any]] = [
            attendance_checked_out(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                attendance_id=closed.id,
                employee_id=closed.employee_id,
                workday=closed.workday,
                duration_minutes=duration_minutes,
                late=closed.late,
                minutes_late=minutes_late,
            )
        ]
        # Same transaction as the close and its audit entry (ADR 0012).
        for envelope in produced:
            await self._outbox.append(envelope)

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="attendance.checked_out",
                entity_type="attendance",
                entity_id=closed.id,
                occurred_at=now,
                changes={
                    "checked_out_at": {"old": None, "new": now.isoformat()},
                    "duration_minutes": {
                        "old": None,
                        "new": closed.duration_minutes,
                    },
                },
            )
        )
        return AttendanceMutation(record=closed, events=tuple(produced))

    # --- reads ---------------------------------------------------------------

    async def list_records(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        late: bool | None = None,
        page: PageParams,
    ) -> AttendanceRecordListPage:
        """Filtered records, scoped to the current user's visibility (T1)."""
        effective = await self._visible_employee_ids(identity, employee_id)
        filters = AttendanceFilters(
            employee_ids=effective,
            from_date=from_date,
            to_date=to_date,
            late=late,
        )
        items, total = await self._repo.list(identity.tenant_id, filters, page)
        return AttendanceRecordListPage(items=list(items), page=page.make_page(total))

    async def board_detail(
        self, identity: Identity, board_date: date, now: datetime
    ) -> BoardDetail:
        """The shared board projection: counts, presence rows, activity.

        One code path behind `GET /attendance/board` and the realtime snapshot,
        so the two surfaces can never disagree (ADR 0009). Counts keep the
        pinned overlapping semantics (not a partition) and are computed over
        each employee's **current** record — the same newest-record basis the
        rows use — so `counts == recount(rows)` by construction and a
        re-check-in replaces its earlier record's contribution instead of
        counting twice. `rows` holds one row per employee with a record for the
        workday — the newest record wins — plus one `on_leave` row per visible
        employee covered by APPROVED leave (overriding any same-day record,
        ADR 0014) plus one `absent` row per currently-absent employee, so
        `absent == len(absent rows)`; `activity` still lists every transition
        (a re-check-in is a real event).
        """
        tenant_id = identity.tenant_id
        visible = await self._scoped_employees(identity)
        employee_ids = (
            [context.id for context in visible]
            if identity.role == Role.MANAGER
            else None
        )
        records = await self._repo.for_workday(tenant_id, board_date, employee_ids)
        shifts = await self._shifts.list(tenant_id)
        shifts_by_id = {shift.id: shift for shift in shifts}
        timezones = {context.id: context.timezone for context in visible}
        # One coverage read per board call; the scope intersection keeps a
        # manager's board to self + direct reports.
        coverage = await self._repo.leave_coverage(tenant_id, board_date)
        covered = {context.id for context in visible if context.id in coverage}

        rows: list[BoardPresenceRow] = []
        current: list[AttendanceRecord] = []
        seen: set[UUID] = set()
        for record in records:
            # `for_workday` is newest-first: the first record per employee is
            # their current presence row (a later re-check-in wins).
            if record.employee_id in seen:
                continue
            seen.add(record.employee_id)
            if record.employee_id in covered:
                # `on_leave` wins over the record (ADR 0014): it contributes no
                # record-based count and its leave row is built below; the
                # record itself stays in the history and the activity feed.
                continue
            current.append(record)
            rows.append(
                _presence_row(
                    record,
                    shifts_by_id.get(record.shift_id)
                    if record.shift_id is not None
                    else None,
                    ZoneInfo(timezones[record.employee_id]),
                )
            )
        # One `on_leave` row per covered visible employee — with or without a
        # record — so `counts == recount(rows)` holds and a consumer delta for
        # a record-less employee survives the next snapshot refresh (ADR 0014).
        for context in visible:
            if context.id not in covered:
                continue
            rows.append(
                BoardPresenceRow(
                    employee_id=context.id,
                    status="on_leave",
                    late=False,
                    minutes_late=None,
                    since=None,
                    attendance_id=None,
                )
            )
        absent_rows = _absent_rows(visible, records, shifts, board_date, now, covered)
        rows.extend(absent_rows)

        counts = BoardCounts(
            present=sum(
                1
                for record in current
                if record.checked_out_at is None and record.work_mode == WorkMode.ONSITE
            ),
            remote=sum(
                1
                for record in current
                if record.checked_out_at is None and record.work_mode == WorkMode.REMOTE
            ),
            late=sum(1 for record in current if record.late),
            on_leave=len(covered),
            absent=len(absent_rows),
            checked_out=sum(
                1 for record in current if record.checked_out_at is not None
            ),
        )

        activity: list[BoardActivity] = []
        for record in records:
            activity.append(
                BoardActivity(
                    occurred_at=record.checked_in_at,
                    employee_id=record.employee_id,
                    action=BoardAction.CHECKED_IN,
                )
            )
            if record.checked_out_at is not None:
                activity.append(
                    BoardActivity(
                        occurred_at=record.checked_out_at,
                        employee_id=record.employee_id,
                        action=BoardAction.CHECKED_OUT,
                    )
                )
        activity.sort(key=lambda entry: entry.occurred_at, reverse=True)
        return BoardDetail(
            date=board_date,
            counts=counts,
            rows=rows,
            activity=activity[:ACTIVITY_LIMIT],
        )

    async def board(self, identity: Identity, board_date: date, now: datetime) -> Board:
        """Live workforce board for one date — the wire mapping of `board_detail`."""
        detail = await self.board_detail(identity, board_date, now)
        return Board(date=detail.date, counts=detail.counts, activity=detail.activity)

    async def visible_employee_scope(
        self, identity: Identity
    ) -> frozenset[UUID] | None:
        """Employee ids the identity's board covers; `None` = tenant-wide.

        The realtime stream filters deltas with exactly the board's scope
        (manager: self + direct reports; unlinked manager: nobody;
        hr/admin/super_admin: every employee), so the live wall and the board
        can never disagree about who is visible. One shared implementation of
        `_scoped_employees`, never a second scope rule.
        """
        if identity.role != Role.MANAGER:
            return None
        return frozenset(
            context.id for context in await self._scoped_employees(identity)
        )

    # --- shared internals ------------------------------------------------------

    async def _visible_employee_ids(
        self, identity: Identity, employee_id: UUID | None
    ) -> Sequence[UUID] | None:
        """Effective list scope; `None` means unscoped (hr/admin/super_admin).

        An `employee_id` outside the current user's visibility is a 404 — existence is
        not leaked to employees, finance or managers.
        """
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            if identity.employee_id is None:
                # No employee link → no visible employees; an explicit id is a
                # 404, never a server error.
                if employee_id is not None:
                    raise EmployeeNotFoundError()
                return []
            if employee_id is not None and employee_id != identity.employee_id:
                raise EmployeeNotFoundError()
            return [identity.employee_id]
        if identity.role == Role.MANAGER:
            visible = await self._scoped_employees(identity)
            visible_ids = [context.id for context in visible]
            if employee_id is not None:
                if employee_id not in visible_ids:
                    raise EmployeeNotFoundError()
                return [employee_id]
            return visible_ids
        return [employee_id] if employee_id is not None else None

    async def _scoped_employees(self, identity: Identity) -> Sequence[EmployeeContext]:
        """Board/list scope: every employee, or a manager's self + reports."""
        if identity.role == Role.MANAGER:
            if identity.employee_id is None:
                # No employee link → a manager sees nobody (`manager_id=None`
                # would mean "all employees" in the repository).
                return []
            return await self._repo.visible_employees(
                identity.tenant_id, manager_id=identity.employee_id
            )
        return await self._repo.visible_employees(identity.tenant_id)

    async def _resolve_shift(self, tenant_id: UUID, location_id: UUID) -> Shift | None:
        """The location's shift, else the tenant default, else none (pinned)."""
        return _expected_shift(await self._shifts.list(tenant_id), location_id)


class ShiftService:
    """Minimal shift reference data (Decision #1): one shift per scope.

    Client-supplied locations resolve tenant-scoped (`404 location_not_found`,
    same rule as every other reference); archived locations stay valid — the
    spec allows them (T1).
    """

    def __init__(
        self,
        repo: ShiftRepository,
        locations: LocationService,
        audit: AuditWriter,
    ) -> None:
        self._repo = repo
        self._locations = locations
        self._audit = audit

    async def list(self, identity: Identity) -> ShiftList:
        return list(await self._repo.list(identity.tenant_id))

    async def create(
        self, identity: Identity, payload: ShiftCreate, now: datetime
    ) -> Shift:
        tenant_id = identity.tenant_id
        if payload.end_time <= payload.start_time:
            raise ShiftTimeInvalidError()
        if payload.location_id is not None:
            await self._locations.get(identity, payload.location_id)
        if await self._repo.scope_taken(tenant_id, payload.location_id):
            raise ShiftScopeTakenError()

        shift = await self._repo.add(
            tenant_id,
            uuid7(),
            name=payload.name,
            start_time=payload.start_time,
            end_time=payload.end_time,
            grace_minutes=payload.grace_minutes,
            location_id=payload.location_id,
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="shift.created",
                entity_type="shift",
                entity_id=shift.id,
                occurred_at=now,
                changes={
                    "name": {"old": None, "new": shift.name},
                    "start_time": {"old": None, "new": _jsonable(shift.start_time)},
                    "end_time": {"old": None, "new": _jsonable(shift.end_time)},
                    "grace_minutes": {"old": None, "new": shift.grace_minutes},
                    "location_id": {"old": None, "new": _ref(shift.location_id)},
                },
            )
        )
        return shift

    async def update(
        self,
        identity: Identity,
        shift_id: UUID,
        payload: ShiftUpdate,
        now: datetime,
    ) -> Shift:
        tenant_id = identity.tenant_id
        existing = await self._repo.get(tenant_id, shift_id)
        if existing is None:
            raise ShiftNotFoundError()
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        start = changes.get("start_time", existing.start_time)
        end = changes.get("end_time", existing.end_time)
        if end <= start:  # validate the merged window, not just the patch
            raise ShiftTimeInvalidError()
        if "location_id" in changes and changes["location_id"] != existing.location_id:
            new_location_id: UUID | None = changes["location_id"]
            if new_location_id is not None:
                # Existence is tenant-scoped; archived locations stay valid.
                await self._locations.get(identity, new_location_id)
            if await self._repo.scope_taken(
                tenant_id, new_location_id, exclude_id=shift_id
            ):
                raise ShiftScopeTakenError()

        updated = await self._repo.update(tenant_id, shift_id, **changes)
        if updated is None:  # pragma: no cover — lost race between get and update
            raise ShiftNotFoundError()
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="shift.updated",
                entity_type="shift",
                entity_id=shift_id,
                occurred_at=now,
                changes={
                    field: {
                        "old": _jsonable(getattr(existing, field)),
                        "new": _jsonable(getattr(updated, field)),
                    }
                    for field in changes
                },
            )
        )
        return updated
