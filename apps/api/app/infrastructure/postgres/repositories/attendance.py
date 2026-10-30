"""Postgres repositories for the Attendance domain.

Every statement carries `WHERE tenant_id = :tenant` — tenant scoping lives
here and nowhere else (`.ai/security.md`). Unique-violation `IntegrityError`s
are mapped to the pinned domain errors (the DB constraint is the race backstop
behind the service pre-checks): the open-record index →
`attendance_already_checked_in`, the shift scope indexes → `shift_scope_taken`;
constraint names are the explicit ones declared in
`domains/attendance/models.py`.

The employee/location reads join `employees` with `locations`: the check-in
workday/lateness judgement and the board's `absent` count both need the
employee's location timezone. The leave-coverage reads join `leave_requests`
(Leave spec T5, ADR 0014): attendance's port, this adapter's join — the
attendance domain itself never imports leave internals.
"""

from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.attendance import models as orm
from app.domains.attendance.exceptions import (
    AttendanceAlreadyCheckedInError,
    ShiftScopeTakenError,
)
from app.domains.attendance.repository import AttendanceFilters, EmployeeContext
from app.domains.attendance.schemas import AttendanceRecord, Shift, WorkMode
from app.domains.employees import models as employees_orm
from app.domains.employees.schemas import EmployeeStatus
from app.domains.leave import models as leave_orm
from app.domains.leave.schemas import LeaveRequestStatus
from app.domains.organizations import models as organizations_orm
from app.shared.errors import WorkPulseError
from app.shared.pagination import PageParams


def _map_unique_violation(exc: IntegrityError) -> WorkPulseError | None:
    """Map a unique-index race to the pinned domain error.

    Asyncpg exposes the violated constraint/index name; the message fallback
    keeps the mapping working for wrapped drivers. Anything else (e.g. a
    foreign-key violation) is not ours to reinterpret — the caller re-raises it
    unchanged.
    """
    haystack = getattr(exc.orig, "constraint_name", None) or str(exc.orig)
    if "uq_attendance_records_open_per_employee" in haystack:
        return AttendanceAlreadyCheckedInError()
    if (
        "uq_shifts_tenant_location" in haystack
        or "uq_shifts_tenant_default" in haystack
    ):
        return ShiftScopeTakenError()
    return None


class PostgresAttendanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID, record_id: UUID) -> AttendanceRecord | None:
        stmt = select(orm.AttendanceRecord).where(
            orm.AttendanceRecord.id == record_id,
            orm.AttendanceRecord.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return AttendanceRecord.model_validate(row) if row is not None else None

    async def open_for_employee(
        self, tenant_id: UUID, employee_id: UUID
    ) -> AttendanceRecord | None:
        stmt = select(orm.AttendanceRecord).where(
            orm.AttendanceRecord.tenant_id == tenant_id,
            orm.AttendanceRecord.employee_id == employee_id,
            orm.AttendanceRecord.checked_out_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return AttendanceRecord.model_validate(row) if row is not None else None

    async def list(
        self, tenant_id: UUID, filters: AttendanceFilters, page: PageParams
    ) -> tuple[Sequence[AttendanceRecord], int]:
        conditions = [orm.AttendanceRecord.tenant_id == tenant_id]
        if filters.employee_ids is not None:
            if not filters.employee_ids:
                return [], 0
            conditions.append(
                orm.AttendanceRecord.employee_id.in_(filters.employee_ids)
            )
        if filters.from_date is not None:
            conditions.append(orm.AttendanceRecord.workday >= filters.from_date)
        if filters.to_date is not None:
            conditions.append(orm.AttendanceRecord.workday <= filters.to_date)
        if filters.late is not None:
            conditions.append(orm.AttendanceRecord.late == filters.late)

        total = (
            await self._session.execute(
                select(func.count())
                .select_from(orm.AttendanceRecord)
                .where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(orm.AttendanceRecord)
                .where(*conditions)
                # Stable pagination (pinned): newest first, id tiebreak.
                .order_by(
                    orm.AttendanceRecord.checked_in_at.desc(),
                    orm.AttendanceRecord.id.desc(),
                )
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return [AttendanceRecord.model_validate(row) for row in rows], total

    async def for_workday(
        self,
        tenant_id: UUID,
        workday: date,
        employee_ids: Sequence[UUID] | None = None,
    ) -> Sequence[AttendanceRecord]:
        if employee_ids is not None and not employee_ids:
            return []
        conditions = [
            orm.AttendanceRecord.tenant_id == tenant_id,
            orm.AttendanceRecord.workday == workday,
        ]
        if employee_ids is not None:
            conditions.append(orm.AttendanceRecord.employee_id.in_(employee_ids))
        stmt = (
            select(orm.AttendanceRecord)
            .where(*conditions)
            .order_by(
                orm.AttendanceRecord.checked_in_at.desc(),
                orm.AttendanceRecord.id.desc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [AttendanceRecord.model_validate(row) for row in rows]

    async def add(
        self,
        tenant_id: UUID,
        record_id: UUID,
        *,
        employee_id: UUID,
        workday: date,
        checked_in_at: datetime,
        work_mode: WorkMode,
        late: bool,
        shift_id: UUID | None,
        check_in_note: str | None,
        minutes_late: int | None = None,
    ) -> AttendanceRecord:
        row = orm.AttendanceRecord(
            id=record_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            workday=workday,
            checked_in_at=checked_in_at,
            checked_out_at=None,
            work_mode=work_mode.value,
            late=late,
            minutes_late=minutes_late,
            shift_id=shift_id,
            check_in_note=check_in_note,
            check_out_note=None,
            duration_minutes=None,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return AttendanceRecord.model_validate(row)

    async def close(
        self,
        tenant_id: UUID,
        record_id: UUID,
        *,
        checked_out_at: datetime,
        duration_minutes: int,
        check_out_note: str | None,
    ) -> AttendanceRecord | None:
        stmt = (
            update(orm.AttendanceRecord)
            .where(
                orm.AttendanceRecord.id == record_id,
                orm.AttendanceRecord.tenant_id == tenant_id,
                # Only an OPEN record may be closed: a concurrent check-out
                # must not be overwritten (returns None → caller re-raises).
                orm.AttendanceRecord.checked_out_at.is_(None),
            )
            .values(
                checked_out_at=checked_out_at,
                duration_minutes=duration_minutes,
                check_out_note=check_out_note,
            )
            .returning(orm.AttendanceRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return AttendanceRecord.model_validate(row) if row is not None else None

    async def employee_context(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeContext | None:
        contexts = await self._context_query(
            employees_orm.Employee.id == employee_id,
            employees_orm.Employee.tenant_id == tenant_id,
        )
        return contexts[0] if contexts else None

    async def covers_leave(
        self, tenant_id: UUID, employee_id: UUID, workday: date
    ) -> bool:
        stmt = (
            select(leave_orm.LeaveRequest.id)
            .where(*self._leave_coverage_conditions(tenant_id, workday))
            .where(leave_orm.LeaveRequest.employee_id == employee_id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def leave_coverage(self, tenant_id: UUID, workday: date) -> set[UUID]:
        stmt = (
            select(leave_orm.LeaveRequest.employee_id)
            .where(*self._leave_coverage_conditions(tenant_id, workday))
            .distinct()
        )
        return set((await self._session.execute(stmt)).scalars())

    @staticmethod
    def _leave_coverage_conditions(tenant_id: UUID, workday: date) -> tuple[Any, ...]:
        """Approved, tenant-scoped leave whose inclusive range covers the date."""
        return (
            leave_orm.LeaveRequest.tenant_id == tenant_id,
            leave_orm.LeaveRequest.status == LeaveRequestStatus.APPROVED.value,
            leave_orm.LeaveRequest.start_date <= workday,
            leave_orm.LeaveRequest.end_date >= workday,
        )

    async def visible_employees(
        self, tenant_id: UUID, *, manager_id: UUID | None = None
    ) -> Sequence[EmployeeContext]:
        conditions = [employees_orm.Employee.tenant_id == tenant_id]
        if manager_id is not None:
            conditions.append(
                or_(
                    employees_orm.Employee.id == manager_id,
                    employees_orm.Employee.manager_id == manager_id,
                )
            )
        return await self._context_query(*conditions)

    async def _context_query(self, *conditions: Any) -> Sequence[EmployeeContext]:
        stmt = (
            select(
                employees_orm.Employee,
                organizations_orm.Location.timezone,
            )
            .join(
                organizations_orm.Location,
                organizations_orm.Location.id == employees_orm.Employee.location_id,
            )
            .where(*conditions)
            .order_by(
                employees_orm.Employee.last_name.asc(),
                employees_orm.Employee.first_name.asc(),
                employees_orm.Employee.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            EmployeeContext(
                id=employee.id,
                status=EmployeeStatus(employee.status),
                location_id=employee.location_id,
                timezone=timezone,
                manager_id=employee.manager_id,
            )
            for employee, timezone in rows
        ]


class PostgresShiftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, tenant_id: UUID) -> Sequence[Shift]:
        stmt = (
            select(orm.Shift)
            .where(orm.Shift.tenant_id == tenant_id)
            # Pinned order: tenant default (null scope) last, then name.
            .order_by(
                orm.Shift.location_id.asc().nulls_last(),
                orm.Shift.name.asc(),
                orm.Shift.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [Shift.model_validate(row) for row in rows]

    async def get(self, tenant_id: UUID, shift_id: UUID) -> Shift | None:
        stmt = select(orm.Shift).where(
            orm.Shift.id == shift_id,
            orm.Shift.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Shift.model_validate(row) if row is not None else None

    async def scope_taken(
        self,
        tenant_id: UUID,
        location_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> bool:
        stmt = select(orm.Shift.id).where(
            orm.Shift.tenant_id == tenant_id,
            (
                orm.Shift.location_id.is_(None)
                if location_id is None
                else orm.Shift.location_id == location_id
            ),
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.Shift.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def add(
        self,
        tenant_id: UUID,
        shift_id: UUID,
        *,
        name: str,
        start_time: time,
        end_time: time,
        grace_minutes: int,
        location_id: UUID | None,
    ) -> Shift:
        row = orm.Shift(
            id=shift_id,
            tenant_id=tenant_id,
            name=name,
            start_time=start_time,
            end_time=end_time,
            grace_minutes=grace_minutes,
            location_id=location_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return Shift.model_validate(row)

    async def update(
        self, tenant_id: UUID, shift_id: UUID, **changes: Any
    ) -> Shift | None:
        stmt = select(orm.Shift).where(
            orm.Shift.id == shift_id,
            orm.Shift.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # scope race after the service pre-check
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return Shift.model_validate(row)
