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

from app.domains.payroll.calculation import CalculatedPayslip
from app.domains.payroll.schemas import (
    PayrollBonus,
    PayrollConfig,
    PayrollDeduction,
    PayrollDeductionKind,
    PayrollRun,
    PayrollRunStatus,
    Payslip,
    PayslipStatus,
    Salary,
    Timesheet,
)
from app.shared.pagination import PageParams


class PayrollRunFilters(BaseModel):
    """Effective run-list filters (tenant scope added by the repository)."""

    period: str | None = None
    status: PayrollRunStatus | None = None


@dataclass(frozen=True, slots=True)
class RunTimesheet:
    """A timesheet row plus the overtime handles the calculation consumed.

    `overtime_rows` is persisted beside the aggregated `overtime_hours` so the
    finalize flip marks exactly the rows this run paid (ADR 0018 decision 2/3)
    — never a row verified after the calculation.
    """

    timesheet: Timesheet
    overtime_rows: Sequence["OvertimeHoursRow"] = ()


@dataclass(frozen=True, slots=True)
class IssuedPayslipRef:
    """The two fields finalize needs to emit one `payslip.issued` event."""

    payslip_id: UUID
    employee_id: UUID


class PayrollRepository(Protocol):
    """Persistence boundary for payroll configuration, inputs and results."""

    # --- configuration ------------------------------------------------------

    async def get_config(self, tenant_id: UUID) -> PayrollConfig | None:
        """The tenant's payroll configuration, or `None` before first write."""
        ...

    async def create_config_if_absent(
        self,
        tenant_id: UUID,
        *,
        currency: str,
        standard_monthly_hours: Decimal,
        overtime_multiplier: Decimal,
        now: datetime,
    ) -> PayrollConfig:
        """Provision the tenant's one config row without overwriting an existing one.

        This is the first-read/first-salary path: `ON CONFLICT DO NOTHING` plus
        a re-read, so a concurrent administrator's update can never be
        clobbered by defaults (the create path must not behave like an update).
        """
        ...

    async def update_config_fields(
        self,
        tenant_id: UUID,
        *,
        currency: str | None = None,
        standard_monthly_hours: Decimal | None = None,
        overtime_multiplier: Decimal | None = None,
        now: datetime,
    ) -> PayrollConfig | None:
        """Patch only the supplied columns; `None` when no config exists.

        A single-column UPDATE keeps concurrent partial updates from restoring
        stale values for fields they did not touch.
        """
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

    async def list_salary_history(
        self, tenant_id: UUID, employee_ids: Sequence[UUID]
    ) -> Sequence[Salary]:
        """Every salary record of the given employees (calculation input).

        The deterministic effective-date pick is the calculator's job; the
        adapter only tenant-scopes and orders by employee/effective date.
        """
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

    async def lock_run(self, tenant_id: UUID, run_id: UUID) -> PayrollRun | None:
        """Read a run under `FOR UPDATE` for a decision + insert race guard.

        `create_bonus` uses this so a concurrent finalize cannot commit between
        the status check and the bonus insert (terminal immutability).
        """
        ...

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

    async def list_employees_in_scope(
        self, tenant_id: UUID, period_start: date, period_end: date
    ) -> Sequence[UUID]:
        """Employees whose employment overlaps the inclusive month.

        `hired_at <= period_end` and (`terminated_at` null or `>= period_start`)
        — a mid-period hire/termination is in scope and gets the full base (no
        proration, PO Decision 5). Status is not filtered: a terminated
        employee whose last day is inside the month is still paid for it.
        """
        ...

    async def tenant_id_for_run(self, run_id: UUID) -> UUID | None:
        """Worker-only cross-tenant lookup (the calculation job has no identity).

        Every other method takes `tenant_id` explicitly; this one exists so
        `calculate_run(run_id, now)` can resolve the tenant of a queued run
        before entering the tenant-scoped repository.
        """
        ...

    async def queued_run_ids(self, limit: int) -> Sequence[UUID]:
        """Worker-only cross-tenant lookup: the oldest `QUEUED` run ids.

        The worker polls this without an identity (it has no request context).
        Claiming is the service's state-guarded `QUEUED → CALCULATING`
        transition, so two workers that see the same id still calculate once.
        """
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

    async def replace_run_results(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        timesheets: Sequence[RunTimesheet],
        payslips: Sequence[CalculatedPayslip],
        now: datetime,
    ) -> bool:
        """Atomically replace a run's timesheets and provisional payslips.

        A recalculation deletes the previous result set and writes the new one
        in the same transaction; the `(run, employee)` uniques are the
        duplicate-pass backstop. Payslip ids are persistence-owned (the
        calculation core has none).

        Returns `False` without writing when the run is no longer
        `CALCULATING`: a duplicate/re-delivered pass that lost the claim must
        never replace a calculated or finalized result set.
        """
        ...

    async def list_run_overtime_rows(
        self, tenant_id: UUID, run_id: UUID
    ) -> Sequence["OvertimeHoursRow"]:
        """The overtime handles this run's calculation consumed (finalize flip)."""
        ...

    async def mark_payslips_issued(
        self, tenant_id: UUID, run_id: UUID, now: datetime
    ) -> Sequence[IssuedPayslipRef]:
        """Flip every provisional payslip of the run to `issued`.

        Returns only the identifiers finalize needs for events — not the full
        payslips with every deduction line.
        """
        ...

    async def supersede_payslips(
        self, tenant_id: UUID, run_id: UUID, now: datetime
    ) -> int:
        """Mark a finalized run's issued payslips `superseded`; return the count."""
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
        self,
        tenant_id: UUID,
        employee_id: UUID,
        page: PageParams,
        *,
        statuses: Sequence[PayslipStatus] | None = None,
    ) -> tuple[Sequence[Payslip], int]:
        """The employee's own payslips, newest period first.

        `statuses` narrows the result (the API passes `issued`/`superseded`
        only, so an employee never sees a provisional draft). `superseded`
        remains readable; `None` means every status.
        """
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


class NoOvertimeHoursPort:
    """P0 placeholder: overtime has no domain yet, so nothing is verified.

    Wired in `app/dependencies.py` until `overtime.md` lands. Returning no
    rows makes payroll's overtime term `0.00` (the plan's pre-P0 behaviour);
    `mark_included` would report zero flips, so a non-empty handle set could
    never silently pass finalize.
    """

    async def verified_hours(
        self, tenant_id: UUID, period: str
    ) -> Sequence[OvertimeHoursRow]:
        return ()

    async def mark_included(
        self,
        tenant_id: UUID,
        rows: Sequence[OvertimeHoursRow],
        run_id: UUID,
    ) -> int:
        return 0


class NoAttendanceHoursPort:
    """Zero-valued attendance seam for callers that do not supply the adapter."""

    async def worked_hours(
        self, tenant_id: UUID, employee_ids: Sequence[UUID], period: str
    ) -> Mapping[UUID, Decimal]:
        return {employee_id: Decimal("0.00") for employee_id in employee_ids}

    async def attendance_days(
        self, tenant_id: UUID, employee_ids: Sequence[UUID], period: str
    ) -> Mapping[UUID, int]:
        return {employee_id: 0 for employee_id in employee_ids}


class NoLeaveDaysPort:
    """Zero-valued approved-leave seam for callers without the adapter."""

    async def approved_leave_days(
        self, tenant_id: UUID, employee_ids: Sequence[UUID], period: str
    ) -> Mapping[UUID, int]:
        return {employee_id: 0 for employee_id in employee_ids}
