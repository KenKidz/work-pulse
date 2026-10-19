"""In-memory fakes for the Attendance domain unit tests.

No I/O anywhere (`.ai/testing.md` — these tests pass with `postgres-test`
stopped). Fakes honor tenant scope, the pinned filters and deterministic
timestamps, and mirror the DB backstops: a second open record per employee
raises `AttendanceAlreadyCheckedInError`, a second shift on the same scope
raises `ShiftScopeTakenError` — the same domain errors the Postgres adapters
map from `IntegrityError`.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from app.domains.attendance.exceptions import (
    AttendanceAlreadyCheckedInError,
    ShiftScopeTakenError,
)
from app.domains.attendance.repository import AttendanceFilters, EmployeeContext
from app.domains.attendance.schemas import AttendanceRecord, Shift, WorkMode
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


class FakeAttendanceRepo:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, UUID], AttendanceRecord] = {}
        self.employees: dict[tuple[UUID, UUID], EmployeeContext] = {}

    def _tenant_records(self, tenant_id: UUID) -> list[AttendanceRecord]:
        return [
            record
            for (row_tenant, _), record in self.records.items()
            if row_tenant == tenant_id
        ]

    async def get(self, tenant_id: UUID, record_id: UUID) -> AttendanceRecord | None:
        return self.records.get((tenant_id, record_id))

    async def open_for_employee(
        self, tenant_id: UUID, employee_id: UUID
    ) -> AttendanceRecord | None:
        return next(
            (
                record
                for record in self._tenant_records(tenant_id)
                if record.employee_id == employee_id and record.checked_out_at is None
            ),
            None,
        )

    async def list(
        self, tenant_id: UUID, filters: AttendanceFilters, page: PageParams
    ) -> tuple[Sequence[AttendanceRecord], int]:
        rows = self._tenant_records(tenant_id)
        if filters.employee_ids is not None:
            allowed = set(filters.employee_ids)
            rows = [row for row in rows if row.employee_id in allowed]
        if filters.from_date is not None:
            rows = [row for row in rows if row.workday >= filters.from_date]
        if filters.to_date is not None:
            rows = [row for row in rows if row.workday <= filters.to_date]
        if filters.late is not None:
            rows = [row for row in rows if row.late is filters.late]
        rows.sort(key=lambda row: (row.checked_in_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def for_workday(
        self,
        tenant_id: UUID,
        workday: date,
        employee_ids: Sequence[UUID] | None = None,
    ) -> Sequence[AttendanceRecord]:
        allowed = None if employee_ids is None else set(employee_ids)
        rows = [
            row
            for row in self._tenant_records(tenant_id)
            if row.workday == workday
            and (allowed is None or row.employee_id in allowed)
        ]
        rows.sort(key=lambda row: (row.checked_in_at, row.id), reverse=True)
        return rows

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
    ) -> AttendanceRecord:
        if await self.open_for_employee(tenant_id, employee_id) is not None:
            # Partial unique index backstop: at most one open record.
            raise AttendanceAlreadyCheckedInError()
        record = AttendanceRecord(
            id=record_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            workday=workday,
            checked_in_at=checked_in_at,
            checked_out_at=None,
            work_mode=work_mode,
            late=late,
            shift_id=shift_id,
            check_in_note=check_in_note,
            check_out_note=None,
            duration_minutes=None,
            created_at=CREATED_AT,
        )
        self.records[(tenant_id, record_id)] = record
        return record

    async def close(
        self,
        tenant_id: UUID,
        record_id: UUID,
        *,
        checked_out_at: datetime,
        duration_minutes: int,
        check_out_note: str | None,
    ) -> AttendanceRecord | None:
        record = self.records.get((tenant_id, record_id))
        if record is None or record.checked_out_at is not None:
            return None  # already closed — never overwrite
        closed = record.model_copy(
            update={
                "checked_out_at": checked_out_at,
                "duration_minutes": duration_minutes,
                "check_out_note": check_out_note,
            }
        )
        self.records[(tenant_id, record_id)] = closed
        return closed

    async def employee_context(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeContext | None:
        return self.employees.get((tenant_id, employee_id))

    async def visible_employees(
        self, tenant_id: UUID, *, manager_id: UUID | None = None
    ) -> Sequence[EmployeeContext]:
        contexts = [
            context
            for (row_tenant, _), context in self.employees.items()
            if row_tenant == tenant_id
        ]
        if manager_id is not None:
            contexts = [
                context
                for context in contexts
                if context.id == manager_id or context.manager_id == manager_id
            ]
        return sorted(contexts, key=lambda context: context.id)


class FakeShiftRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], Shift] = {}

    async def list(self, tenant_id: UUID) -> Sequence[Shift]:
        rows = [
            row for (row_tenant, _), row in self.rows.items() if row_tenant == tenant_id
        ]
        # Pinned order: tenant default (null scope) last, then name.
        rows.sort(
            key=lambda row: (
                row.location_id is None,
                str(row.location_id or ""),
                row.name,
                row.id,
            )
        )
        return rows

    async def get(self, tenant_id: UUID, shift_id: UUID) -> Shift | None:
        return self.rows.get((tenant_id, shift_id))

    async def scope_taken(
        self,
        tenant_id: UUID,
        location_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> bool:
        return any(
            row.location_id == location_id and row.id != exclude_id
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        )

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
        if await self.scope_taken(tenant_id, location_id):
            raise ShiftScopeTakenError()  # partial unique index backstop
        row = Shift(
            id=shift_id,
            tenant_id=tenant_id,
            name=name,
            start_time=start_time,
            end_time=end_time,
            grace_minutes=grace_minutes,
            location_id=location_id,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )
        self.rows[(tenant_id, shift_id)] = row
        return row

    async def update(
        self, tenant_id: UUID, shift_id: UUID, **changes: Any
    ) -> Shift | None:
        key = (tenant_id, shift_id)
        row = self.rows.get(key)
        if row is None:
            return None
        if (
            "location_id" in changes
            and changes["location_id"] != row.location_id
            and await self.scope_taken(
                tenant_id, changes["location_id"], exclude_id=shift_id
            )
        ):
            raise ShiftScopeTakenError()
        updated = row.model_copy(update={**changes, "updated_at": UPDATED_AT})
        self.rows[key] = updated
        return updated
