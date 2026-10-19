"""Data-access abstractions for the Attendance domain.

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the wire schemas (`schemas.py`); ORM translation happens
inside the Postgres adapters.

`AttendanceFilters` carries the *effective* filters: the service resolves the
current user's visibility scope into `employee_ids` (T1 role matrix) and only
then calls `list`. `EmployeeContext` is the employer/location read attendance rules
need — the employee row joined with their location's timezone, which the
check-in workday/lateness judgement and the board's `absent` count depend on.

`covers_leave`/`leave_coverage` are the Leave-context reads attendance rules
need (Leave spec T5, ADR 0014): the adapter joins `leave_requests`
(`status='approved'`, range covering the date) — the same cross-domain read
pattern as `EmployeeContext`, so the domain never imports leave internals.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.attendance.schemas import AttendanceRecord, Shift, WorkMode
from app.domains.employees.schemas import EmployeeStatus
from app.shared.pagination import PageParams


class AttendanceFilters(BaseModel):
    """Effective record-list filters (visibility already resolved).

    `employee_ids is None` means "no employee scoping" (hr/admin/super_admin);
    an empty sequence matches nothing. `from_date`/`to_date` filter `workday`
    inclusively.
    """

    employee_ids: Sequence[UUID] | None = None
    from_date: date | None = None
    to_date: date | None = None
    late: bool | None = None


@dataclass(frozen=True, slots=True)
class EmployeeContext:
    """Employee facts attendance rules need, joined with the location timezone."""

    id: UUID
    status: EmployeeStatus
    location_id: UUID
    timezone: str
    manager_id: UUID | None


class AttendanceRepository(Protocol):
    async def get(
        self, tenant_id: UUID, record_id: UUID
    ) -> AttendanceRecord | None: ...

    async def open_for_employee(
        self, tenant_id: UUID, employee_id: UUID
    ) -> AttendanceRecord | None:
        """The employee's open record (`checked_out_at IS NULL`), if any."""
        ...

    async def list(
        self, tenant_id: UUID, filters: AttendanceFilters, page: PageParams
    ) -> tuple[Sequence[AttendanceRecord], int]:
        """Records ordered `checked_in_at` desc, `id` desc (stable pagination)."""
        ...

    async def for_workday(
        self,
        tenant_id: UUID,
        workday: date,
        employee_ids: Sequence[UUID] | None = None,
    ) -> Sequence[AttendanceRecord]:
        """Board read: every record of the workday, optionally scoped."""
        ...

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
        """Insert the open record; the open-record unique index is the backstop."""
        ...

    async def close(
        self,
        tenant_id: UUID,
        record_id: UUID,
        *,
        checked_out_at: datetime,
        duration_minutes: int,
        check_out_note: str | None,
    ) -> AttendanceRecord | None:
        """Close an open record; `None` when it was already closed/nonexistent."""
        ...

    async def employee_context(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeContext | None: ...

    async def visible_employees(
        self, tenant_id: UUID, *, manager_id: UUID | None = None
    ) -> Sequence[EmployeeContext]:
        """Board scope: all employees, or a manager's self + direct reports."""
        ...

    async def covers_leave(
        self, tenant_id: UUID, employee_id: UUID, workday: date
    ) -> bool:
        """Whether the employee has APPROVED leave covering `workday`.

        Only `APPROVED` blocks a check-in (`PENDING` never reaches the wall);
        the inclusive range check is `start_date <= workday <= end_date`
        (ADR 0014). A plain read — no leave internals cross the domain line.
        """
        ...

    async def leave_coverage(self, tenant_id: UUID, workday: date) -> set[UUID]:
        """Employee ids whose APPROVED leave covers `workday` (board read).

        One tenant-scoped read per board call; the service intersects it with
        the board's visible scope and `on_leave` wins over a same-day record
        (ADR 0014).
        """
        ...


class ShiftRepository(Protocol):
    """Shift reference data. `update(**changes)` writes only the keys present.

    Allowed keys: `name`, `start_time`, `end_time`, `grace_minutes`,
    `location_id`. Scope uniqueness (`shift_scope_taken`) is enforced by
    partial unique indexes; the service pre-checks via `scope_taken`.
    """

    async def list(self, tenant_id: UUID) -> Sequence[Shift]:
        """All shifts, ordered `location_id` nulls-last then `name`."""
        ...

    async def get(self, tenant_id: UUID, shift_id: UUID) -> Shift | None: ...

    async def scope_taken(
        self,
        tenant_id: UUID,
        location_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> bool:
        """Whether a shift already holds this scope (tenant default when null)."""
        ...

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
    ) -> Shift: ...

    async def update(
        self, tenant_id: UUID, shift_id: UUID, **changes: Any
    ) -> Shift | None: ...
