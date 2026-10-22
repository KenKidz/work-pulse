"""Postgres adapters for payroll's seam ports (ADR 0018).

Payroll owns the Protocols (`domains/payroll/repository.py`); these adapters
perform the cross-context joins, exactly like the attendance↔leave coverage
seam (ADR 0014). The payroll domain never imports attendance/leave/overtime
models.

`PostgresOvertimeHoursPort` is the second half of the ADR 0018 overtime seam:
it reads the month's `hr_verified` rows and performs the finalize flip to
`included_in_payroll` with the run handle. The write is state-guarded,
idempotent for the same run, and can never count a row another finalized run
already included.
"""

from calendar import monthrange
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.attendance import models as attendance_orm
from app.domains.leave import models as leave_orm
from app.domains.leave.schemas import LeaveRequestStatus
from app.domains.overtime import models as overtime_orm
from app.domains.overtime.schemas import OvertimeRequestStatus
from app.domains.payroll.repository import OvertimeHoursRow

_CENTIHOUR = Decimal("0.01")
# Finalize flips one run's handles in bounded statements: a large monthly run
# can exceed PostgreSQL/asyncpg's bind-parameter limit with a single IN list.
_OVERTIME_FLIP_CHUNK = 4000


def _period_bounds(period: str) -> tuple[date, date]:
    """`YYYY-MM` → its inclusive first/last calendar day."""
    year_text, month_text = period.split("-", 1)
    year, month = int(year_text), int(month_text)
    return (
        date(year, month, 1),
        date(year, month, monthrange(year, month)[1]),
    )


def _quantize_hours(value: Decimal | float | int) -> Decimal:
    """Hours are informational; render them at the same 2dp convention."""
    return Decimal(str(value)).quantize(_CENTIHOUR, rounding=ROUND_HALF_UP)


class PostgresAttendanceHoursPort:
    """Closed attendance records → worked hours and attendance days."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def worked_hours(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, Decimal]:
        if not employee_ids:
            return {}
        period_start, period_end = _period_bounds(period)
        duration_hours = (
            func.extract(
                "epoch",
                attendance_orm.AttendanceRecord.checked_out_at
                - attendance_orm.AttendanceRecord.checked_in_at,
            )
            / 3600
        )
        stmt = (
            select(
                attendance_orm.AttendanceRecord.employee_id,
                func.sum(duration_hours),
            )
            .where(
                attendance_orm.AttendanceRecord.tenant_id == tenant_id,
                attendance_orm.AttendanceRecord.employee_id.in_(employee_ids),
                attendance_orm.AttendanceRecord.checked_out_at.is_not(None),
                attendance_orm.AttendanceRecord.workday >= period_start,
                attendance_orm.AttendanceRecord.workday <= period_end,
            )
            .group_by(attendance_orm.AttendanceRecord.employee_id)
        )
        rows = (await self._session.execute(stmt)).all()
        totals: dict[UUID, Decimal] = {
            employee_id: Decimal("0.00") for employee_id in employee_ids
        }
        for employee_id, total in rows:
            totals[employee_id] = _quantize_hours(total)
        return totals

    async def attendance_days(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, int]:
        if not employee_ids:
            return {}
        period_start, period_end = _period_bounds(period)
        stmt = (
            select(
                attendance_orm.AttendanceRecord.employee_id,
                func.count(),
            )
            .where(
                attendance_orm.AttendanceRecord.tenant_id == tenant_id,
                attendance_orm.AttendanceRecord.employee_id.in_(employee_ids),
                attendance_orm.AttendanceRecord.checked_out_at.is_not(None),
                attendance_orm.AttendanceRecord.workday >= period_start,
                attendance_orm.AttendanceRecord.workday <= period_end,
            )
            .group_by(attendance_orm.AttendanceRecord.employee_id)
        )
        rows = (await self._session.execute(stmt)).all()
        days: dict[UUID, int] = {employee_id: 0 for employee_id in employee_ids}
        for employee_id, count in rows:
            days[employee_id] = int(count)
        return days


class PostgresLeaveDaysPort:
    """Approved leave ranges → distinct covered calendar days in a month."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def approved_leave_days(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, int]:
        if not employee_ids:
            return {}
        period_start, period_end = _period_bounds(period)
        stmt = select(
            leave_orm.LeaveRequest.employee_id,
            leave_orm.LeaveRequest.start_date,
            leave_orm.LeaveRequest.end_date,
        ).where(
            leave_orm.LeaveRequest.tenant_id == tenant_id,
            leave_orm.LeaveRequest.employee_id.in_(employee_ids),
            leave_orm.LeaveRequest.status == LeaveRequestStatus.APPROVED.value,
            # Inclusive overlap with the month; clipping happens below so
            # overlapping approved ranges are a union, never a sum.
            leave_orm.LeaveRequest.start_date <= period_end,
            leave_orm.LeaveRequest.end_date >= period_start,
        )
        rows = (await self._session.execute(stmt)).all()

        intervals: dict[UUID, list[tuple[date, date]]] = defaultdict(list)
        for employee_id, start_date, end_date in rows:
            intervals[employee_id].append(
                (max(start_date, period_start), min(end_date, period_end))
            )

        days: dict[UUID, int] = {}
        for employee_id in employee_ids:
            days[employee_id] = _union_days(intervals.get(employee_id, []))
        return days


class PostgresOvertimeHoursPort:
    """Payroll's HR-verified overtime seam: month read + guarded finalize flip.

    `verified_hours` returns only rows still in `hr_verified` for the month
    (lowercase storage; ADR 0018's `'HR_VERIFIED'` is enum notation), ordered
    deterministically so a calculation's stored handles are stable.

    `mark_included` flips exactly the supplied handles whose current state is
    `hr_verified` and counts rows already included under the **same** run as
    idempotent successes. A row included by another run, or in any other
    state, is never counted — the at-most-one-finalized-run guard (ADR 0018
    decision 4), which makes payroll's `flipped == len(rows)` check exact.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def verified_hours(
        self, tenant_id: UUID, period: str
    ) -> Sequence[OvertimeHoursRow]:
        period_start, period_end = _period_bounds(period)
        stmt = (
            select(
                overtime_orm.OvertimeRequest.id,
                overtime_orm.OvertimeRequest.employee_id,
                overtime_orm.OvertimeRequest.hours,
            )
            .where(
                overtime_orm.OvertimeRequest.tenant_id == tenant_id,
                overtime_orm.OvertimeRequest.status
                == OvertimeRequestStatus.HR_VERIFIED.value,
                overtime_orm.OvertimeRequest.work_date >= period_start,
                overtime_orm.OvertimeRequest.work_date <= period_end,
            )
            .order_by(
                overtime_orm.OvertimeRequest.work_date.asc(),
                overtime_orm.OvertimeRequest.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            OvertimeHoursRow(
                overtime_id=overtime_id,
                employee_id=employee_id,
                hours=hours,
            )
            for overtime_id, employee_id, hours in rows
        ]

    async def mark_included(
        self,
        tenant_id: UUID,
        rows: Sequence[OvertimeHoursRow],
        run_id: UUID,
    ) -> int:
        # One handle per row; duplicate ids are dropped so a malformed caller
        # can never be counted twice by two chunks.
        overtime_ids = list(dict.fromkeys(row.overtime_id for row in rows))
        if not overtime_ids:
            return 0

        counted = 0
        for start in range(0, len(overtime_ids), _OVERTIME_FLIP_CHUNK):
            chunk = overtime_ids[start : start + _OVERTIME_FLIP_CHUNK]
            flip = (
                update(overtime_orm.OvertimeRequest)
                .where(
                    overtime_orm.OvertimeRequest.tenant_id == tenant_id,
                    overtime_orm.OvertimeRequest.id.in_(chunk),
                    overtime_orm.OvertimeRequest.status
                    == OvertimeRequestStatus.HR_VERIFIED.value,
                )
                .values(
                    status=OvertimeRequestStatus.INCLUDED_IN_PAYROLL.value,
                    included_in_run_id=run_id,
                )
                .returning(overtime_orm.OvertimeRequest.id)
            )
            flipped = set((await self._session.execute(flip)).scalars())
            counted += len(flipped)

            remaining = [
                overtime_id for overtime_id in chunk if overtime_id not in flipped
            ]
            if not remaining:
                continue

            # A replay of the same finalize is a no-op on state but still
            # counts: those rows are already included by *this* run.
            already = select(overtime_orm.OvertimeRequest.id).where(
                overtime_orm.OvertimeRequest.tenant_id == tenant_id,
                overtime_orm.OvertimeRequest.id.in_(remaining),
                overtime_orm.OvertimeRequest.status
                == OvertimeRequestStatus.INCLUDED_IN_PAYROLL.value,
                overtime_orm.OvertimeRequest.included_in_run_id == run_id,
            )
            already_ids = set((await self._session.execute(already)).scalars())
            counted += len(already_ids)
        return counted


def _union_days(intervals: Sequence[tuple[date, date]]) -> int:
    """Count the inclusive union of clipped date ranges."""
    if not intervals:
        return 0
    ordered = sorted(intervals)
    total = 0
    current_start, current_end = ordered[0]
    for start_date, end_date in ordered[1:]:
        if start_date <= current_end + timedelta(days=1):
            current_end = max(current_end, end_date)
            continue
        total += (current_end - current_start).days + 1
        current_start, current_end = start_date, end_date
    return total + (current_end - current_start).days + 1
