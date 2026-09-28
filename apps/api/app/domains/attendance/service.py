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
- **Self-only capture**: check-in/check-out resolve the actor's own employee
  from `X-Actor-Id`; a non-active employee cannot check in or out.
- **Visibility** (T1 role matrix): `employee`/`finance` see their own records;
  `manager` sees self + direct reports; `hr`/`admin`/`super_admin` see all. An
  out-of-scope `employee_id` filter is a `404 employee_not_found` — existence
  is not leaked.
- **Board counts are independent, not a partition** (a person may be `present`
  and `late`): `absent` needs an expected shift, zero records for the date and
  `now` past `start + grace` in the employee's location timezone; `on_leave` is
  always 0 in Phase 1.
- **Shift scopes**: at most one shift per scope (shared indexes/checks in the
  repository) and a client-supplied `location_id` must exist in the actor's
  tenant → `404 location_not_found`; archived locations stay referenceable.
- **Audit**: every check-in/check-out writes an entry with ids/flags only —
  note values are PII and never reach `changes`, logs or events.
- **Events**: mutations build their T2 payloads (contracts exist in Phase 1,
  the bus arrives in Phase 3) and return them on `AttendanceMutation`; nothing
  is published here.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
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
from app.shared.identity import DevIdentity, Role
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


@dataclass(frozen=True, slots=True)
class AttendanceMutation:
    """A completed check-in/check-out plus the event payloads it produced.

    Phase 1 builds but never publishes (contracts and builders land now, the
    bus in Phase 3); returning them keeps the produced facts observable and is
    exactly what Phase 3's outbox publisher will consume.
    """

    record: AttendanceRecord
    events: tuple[dict[str, Any], ...]


class AttendanceService:
    def __init__(
        self,
        repo: AttendanceRepository,
        shifts: ShiftRepository,
        audit: AuditWriter,
    ) -> None:
        self._repo = repo
        self._shifts = shifts
        self._audit = audit

    # --- captures ------------------------------------------------------------

    async def check_in(
        self, identity: DevIdentity, payload: CheckInRequest, now: datetime
    ) -> AttendanceMutation:
        """Open a record for the actor — self-only, server time only."""
        tenant_id = identity.tenant_id
        context = await self._repo.employee_context(tenant_id, identity.actor_id)
        if context is None:
            raise EmployeeNotFoundError()
        if context.status != EmployeeStatus.ACTIVE:
            raise EmployeeNotActiveError()
        if await self._repo.open_for_employee(tenant_id, context.id) is not None:
            raise AttendanceAlreadyCheckedInError()
        try:
            work_mode = WorkMode(payload.work_mode)
        except ValueError:
            raise InvalidWorkModeError() from None

        location_timezone = ZoneInfo(context.timezone)
        workday = now.astimezone(location_timezone).date()
        shift = await self._resolve_shift(tenant_id, context.location_id)
        late = False
        minutes_late = 0
        if shift is not None:
            boundary = self._shift_boundary(shift, workday, location_timezone)
            if now > boundary:
                late = True
                minutes_late = int((now - boundary).total_seconds() // 60)

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
        identity: DevIdentity,
        record_id: UUID,
        payload: CheckOutRequest,
        now: datetime,
    ) -> AttendanceMutation:
        """Close the actor's open record; the workday is never recomputed."""
        tenant_id = identity.tenant_id
        record = await self._repo.get(tenant_id, record_id)
        if record is None or record.employee_id != identity.actor_id:
            # Someone else's record is a 404 — its existence is not leaked.
            raise AttendanceRecordNotFoundError()
        if record.checked_out_at is not None:
            raise AttendanceNotOpenError()

        context = await self._repo.employee_context(tenant_id, identity.actor_id)
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
        return AttendanceMutation(
            record=closed,
            events=(
                attendance_checked_out(
                    tenant_id=tenant_id,
                    actor_id=identity.actor_id,
                    occurred_at=now,
                    attendance_id=closed.id,
                    employee_id=closed.employee_id,
                    workday=closed.workday,
                    duration_minutes=duration_minutes,
                ),
            ),
        )

    # --- reads ---------------------------------------------------------------

    async def list_records(
        self,
        identity: DevIdentity,
        *,
        employee_id: UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        late: bool | None = None,
        page: PageParams,
    ) -> AttendanceRecordListPage:
        """Filtered records, scoped to the actor's visibility (T1 role matrix)."""
        effective = await self._visible_employee_ids(identity, employee_id)
        filters = AttendanceFilters(
            employee_ids=effective,
            from_date=from_date,
            to_date=to_date,
            late=late,
        )
        items, total = await self._repo.list(identity.tenant_id, filters, page)
        return AttendanceRecordListPage(items=list(items), page=page.make_page(total))

    async def board(
        self, identity: DevIdentity, board_date: date, now: datetime
    ) -> Board:
        """Live workforce board for one date, scoped to visible employees."""
        tenant_id = identity.tenant_id
        visible = await self._scoped_employees(identity)
        employee_ids = (
            [context.id for context in visible]
            if identity.role == Role.MANAGER
            else None
        )
        records = await self._repo.for_workday(tenant_id, board_date, employee_ids)

        present = sum(
            1
            for record in records
            if record.checked_out_at is None and record.work_mode == WorkMode.ONSITE
        )
        remote = sum(
            1
            for record in records
            if record.checked_out_at is None and record.work_mode == WorkMode.REMOTE
        )
        checked_out = sum(1 for record in records if record.checked_out_at is not None)
        counts = BoardCounts(
            present=present,
            remote=remote,
            late=sum(1 for record in records if record.late),
            on_leave=0,  # Leave lands in Phase 3 — the field keeps the contract
            absent=await self._absent_count(
                tenant_id, visible, records, board_date, now
            ),
            checked_out=checked_out,
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
        return Board(date=board_date, counts=counts, activity=activity[:ACTIVITY_LIMIT])

    # --- shared internals ------------------------------------------------------

    async def _visible_employee_ids(
        self, identity: DevIdentity, employee_id: UUID | None
    ) -> Sequence[UUID] | None:
        """Effective list scope; `None` means unscoped (hr/admin/super_admin).

        An `employee_id` outside the actor's visibility is a 404 — existence is
        not leaked to employees, finance or managers.
        """
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            if employee_id is not None and employee_id != identity.actor_id:
                raise EmployeeNotFoundError()
            return [identity.actor_id]
        if identity.role == Role.MANAGER:
            visible = await self._scoped_employees(identity)
            visible_ids = [context.id for context in visible]
            if employee_id is not None:
                if employee_id not in visible_ids:
                    raise EmployeeNotFoundError()
                return [employee_id]
            return visible_ids
        return [employee_id] if employee_id is not None else None

    async def _scoped_employees(
        self, identity: DevIdentity
    ) -> Sequence[EmployeeContext]:
        """Board/list scope: every employee, or a manager's self + reports."""
        if identity.role == Role.MANAGER:
            return await self._repo.visible_employees(
                identity.tenant_id, manager_id=identity.actor_id
            )
        return await self._repo.visible_employees(identity.tenant_id)

    async def _absent_count(
        self,
        tenant_id: UUID,
        visible: Sequence[EmployeeContext],
        records: Sequence[AttendanceRecord],
        board_date: date,
        now: datetime,
    ) -> int:
        """Active employees past `shift.start + grace` with zero records.

        Shifts resolve as the employee's location's shift, else the tenant
        default, else none (never absent). Counts employees, not records.
        """
        shifts = await self._shifts.list(tenant_id)
        recorded = {record.employee_id for record in records}
        absent = 0
        for context in visible:
            if context.status != EmployeeStatus.ACTIVE or context.id in recorded:
                continue
            shift = _expected_shift(shifts, context.location_id)
            if shift is None:
                continue
            boundary = self._shift_boundary(
                shift, board_date, ZoneInfo(context.timezone)
            )
            if now > boundary:
                absent += 1
        return absent

    async def _resolve_shift(self, tenant_id: UUID, location_id: UUID) -> Shift | None:
        """The location's shift, else the tenant default, else none (pinned)."""
        return _expected_shift(await self._shifts.list(tenant_id), location_id)

    @staticmethod
    def _shift_boundary(shift: Shift, workday: date, timezone: ZoneInfo) -> datetime:
        """`shift.start_time + grace_minutes` on `workday` in the location tz."""
        start = datetime.combine(workday, shift.start_time, tzinfo=timezone)
        return start + timedelta(minutes=shift.grace_minutes)


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

    async def list(self, identity: DevIdentity) -> ShiftList:
        return list(await self._repo.list(identity.tenant_id))

    async def create(
        self, identity: DevIdentity, payload: ShiftCreate, now: datetime
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
        identity: DevIdentity,
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
