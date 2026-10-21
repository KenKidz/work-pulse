"""Data-access abstractions for the Payroll domain (T3, ADR 0018).

Protocols only — the domain never imports infrastructure. Every payroll
method takes `tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
The repository speaks the pinned wire schemas (`schemas.py`); ORM translation
happens inside the Postgres adapter.

Three **seam ports** carry payroll's cross-context inputs (ADR 0018, extending
ADR 0014's read-only pattern with one guarded write):

- `OvertimeHoursPort` — HR-verified overtime rows for a period, plus the
  finalize flip to `INCLUDED_IN_PAYROLL` with `included_in_run_id`. The
  overtime feature (`overtime.md`, P0) owns the rows; payroll owns this
  Protocol and the infrastructure adapter lands with P0. Until then the
  formula term is exercised through fakes and real-hours e2e stays at `0.00`.
- `AttendanceHoursPort` — closed attendance facts (worked hours, attendance
  days) by check-in workday.
- `LeaveDaysPort` — distinct approved-leave calendar days, a union rather than
  a sum.

`verified_hours` returns row handles, not just a total, so finalize can flip
exactly the rows the calculation consumed (ADR 0018 decision 2). The write is
state-guarded and transactional with the run finalize.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.payroll.schemas import (
    PayrollBonus,
    PayrollConfig,
    PayrollDeduction,
    PayrollDeductionKind,
    PayrollRun,
    PayrollRunStatus,
    Payslip,
    Salary,
    Timesheet,
)
from app.shared.pagination import PageParams


class PayrollRunFilters(BaseModel):
    """Effective run-list filters (tenant scope added by the repository)."""

    period: str | None = None
    status: PayrollRunStatus | None = None


class PayrollRepository(Protocol):
    """Persistence boundary for payroll configuration, inputs and results."""

    # --- configuration ------------------------------------------------------

    async def get_config(self, tenant_id: UUID) -> PayrollConfig | None:
        """The tenant's payroll configuration, or `None` before first write."""
        ...

    async def upsert_config(
        self,
        tenant_id: UUID,
        *,
        currency: str,
        standard_monthly_hours: Decimal,
        overtime_multiplier: Decimal,
        now: datetime,
    ) -> PayrollConfig:
        """Create or replace the one configuration row for the tenant."""
        ...

    # --- deduction lines ----------------------------------------------------

    async def list_deductions(self, tenant_id: UUID) -> Sequence[PayrollDeduction]:
        """All deduction lines, ordered by `code` for deterministic output."""
        ...

    async def get_deduction(
        self, tenant_id: UUID, deduction_id: UUID
    ) -> PayrollDeduction | None: ...

    async def deduction_code_taken(
        self, tenant_id: UUID, code: str, exclude_id: UUID | None = None
    ) -> bool:
        """Whether the tenant already has this code (create pre-check)."""
        ...

    async def add_deduction(
        self,
        tenant_id: UUID,
        deduction_id: UUID,
        *,
        code: str,
        name: str,
        kind: PayrollDeductionKind,
        value: Decimal,
        active: bool,
        now: datetime,
    ) -> PayrollDeduction: ...

    async def update_deduction(
        self,
        tenant_id: UUID,
        deduction_id: UUID,
        *,
        name: str | None = None,
        kind: PayrollDeductionKind | None = None,
        value: Decimal | None = None,
        active: bool | None = None,
        now: datetime,
    ) -> PayrollDeduction | None:
        """Patch only the supplied fields; `None` when the row is not found."""
        ...

    # --- salary records -----------------------------------------------------

    async def list_salaries(
        self,
        tenant_id: UUID,
        *,
        employee_id: UUID | None,
        page: PageParams,
    ) -> tuple[Sequence[Salary], int]:
        """Paged salary history, newest `effective_from` first."""
        ...

    async def add_salary(
        self,
        tenant_id: UUID,
        salary_id: UUID,
        *,
        employee_id: UUID,
        amount: Decimal,
        currency: str,
        effective_from: date,
        created_by: UUID,
        now: datetime,
    ) -> Salary:
        """Append one salary record (the unique key is the race backstop)."""
        ...

    async def salary_effective_date_taken(
        self, tenant_id: UUID, employee_id: UUID, effective_from: date
    ) -> bool:
        """Whether this employee already has a record on the date."""
        ...

    async def employee_exists(self, tenant_id: UUID, employee_id: UUID) -> bool:
        """Tenant-scoped employee existence for salary/bonus targets."""
        ...

    # --- runs ---------------------------------------------------------------

    async def add_run(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        period: str,
        created_by: UUID,
        supersedes_run_id: UUID | None,
        now: datetime,
    ) -> PayrollRun:
        """Insert a `queued` run (the partial unique index is the backstop)."""
        ...

    async def get_run(self, tenant_id: UUID, run_id: UUID) -> PayrollRun | None: ...

    async def list_runs(
        self, tenant_id: UUID, filters: PayrollRunFilters, page: PageParams
    ) -> tuple[Sequence[PayrollRun], int]:
        """Paged runs, newest `period` then `created_at` first."""
        ...

    async def open_run_for_period(
        self, tenant_id: UUID, period: str
    ) -> PayrollRun | None:
        """The non-finalized run for the period (`payroll_period_open`)."""
        ...

    async def latest_finalized_run(
        self, tenant_id: UUID, period: str
    ) -> PayrollRun | None:
        """The finalized run this period's correction run would supersede."""
        ...

    async def update_run(
        self, tenant_id: UUID, run_id: UUID, **changes: Any
    ) -> PayrollRun | None:
        """Patch a run; allowed keys are the non-tenant columns of the wire
        shape (`status`, counts, `skipped`, `totals`, `failed_code`, timestamps,
        `superseded_by_run_id`). `None` when the run is not in the tenant."""
        ...

    async def transition_run(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        from_statuses: Sequence[PayrollRunStatus],
        to_status: PayrollRunStatus,
        now: datetime,
        **changes: Any,
    ) -> PayrollRun | None:
        """Conditional lifecycle transition (`WHERE status IN from_statuses`).

        `None` when a concurrent writer already moved the run or the id is not
        in the tenant. This is the exactly-one-wins primitive for finalize and
        the worker's state-guarded claim (ADR 0018 / spec R6).
        """
        ...

    # --- bonuses ------------------------------------------------------------

    async def add_bonus(
        self,
        tenant_id: UUID,
        bonus_id: UUID,
        *,
        run_id: UUID,
        employee_id: UUID,
        amount: Decimal,
        note: str | None,
        created_by: UUID,
        now: datetime,
    ) -> PayrollBonus: ...

    async def list_bonuses(
        self, tenant_id: UUID, run_id: UUID
    ) -> Sequence[PayrollBonus]:
        """All bonus entries of a run, oldest first (deterministic sum order)."""
        ...

    async def payslip_exists(
        self, tenant_id: UUID, run_id: UUID, employee_id: UUID
    ) -> bool:
        """Whether the employee is in the run (`payroll_employee_not_in_run`)."""
        ...

    async def list_run_timesheets(
        self, tenant_id: UUID, run_id: UUID, page: PageParams
    ) -> tuple[Sequence[Timesheet], int]:
        """Paged timesheets of one run, ordered by employee id."""
        ...

    async def list_run_payslips(
        self, tenant_id: UUID, run_id: UUID, page: PageParams
    ) -> tuple[Sequence[Payslip], int]:
        """Paged payslips of one run, ordered by employee id."""
        ...

    async def list_employee_payslips(
        self, tenant_id: UUID, employee_id: UUID, page: PageParams
    ) -> tuple[Sequence[Payslip], int]:
        """The employee's own payslips, newest period first (includes
        `superseded`)."""
        ...

    async def get_payslip(self, tenant_id: UUID, payslip_id: UUID) -> Payslip | None:
        """Tenant-scoped payslip with its ordered deduction lines."""
        ...


# --- cross-context seam (ADR 0018) ------------------------------------------


@dataclass(frozen=True, slots=True)
class OvertimeHoursRow:
    """One HR-verified overtime row handle (the unit of the finalize flip)."""

    overtime_id: UUID
    employee_id: UUID
    hours: Decimal


class OvertimeHoursPort(Protocol):
    """Payroll-owned protocol over the overtime feature's verified rows."""

    async def verified_hours(
        self, tenant_id: UUID, period: str
    ) -> Sequence[OvertimeHoursRow]:
        """Rows in `HR_VERIFIED` for the month, excluding already-included
        rows, so a correction run pays only newly verified hours."""
        ...

    async def mark_included(
        self,
        tenant_id: UUID,
        rows: Sequence[OvertimeHoursRow],
        run_id: UUID,
    ) -> int:
        """Flip exactly `rows` to `INCLUDED_IN_PAYROLL` for `run_id`.

        State-guarded (`WHERE status = 'HR_VERIFIED'`) and idempotent for the
        same run. Returns the number of rows flipped; the finalize fails and
        rolls back if this is less than `len(rows)` (ADR 0018 decision 3).
        """
        ...


class AttendanceHoursPort(Protocol):
    """Closed-attendance facts for a run's employee scope (informational)."""

    async def worked_hours(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, Decimal]:
        """Σ `(check_out − check_in)` over closed records whose check-in workday
        is inside the month. Every requested id is present, zero by default."""
        ...

    async def attendance_days(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, int]:
        """Count of those closed records per employee, zero by default."""
        ...


class LeaveDaysPort(Protocol):
    """Approved-leave calendar-day coverage for a run's employee scope."""

    async def approved_leave_days(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, int]:
        """Distinct days inside the month covered by `APPROVED` leave.

        Overlapping approved ranges are a union, never a sum. Every requested
        id is present, zero by default.
        """
        ...
