"""In-memory fakes for the Payroll unit tests (no database, no broker).

The payroll service is fully testable before the overtime feature exists
(ADR 0018 decision 7): every repository and seam port has a deterministic
in-memory implementation here. Fakes honor tenant scope, stable ordering and
the state-guarded transition semantics so tests assert behavior, not SQL.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domains.payroll.repository import (
    OvertimeHoursRow,
    PayrollRunFilters,
)
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

CREATED_AT = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


class FakePayrollRepository:
    """Tenant-scoped in-memory implementation of `PayrollRepository`."""

    def __init__(self) -> None:
        self.configs: dict[UUID, PayrollConfig] = {}
        self.deductions: dict[tuple[UUID, UUID], PayrollDeduction] = {}
        self.salaries: dict[tuple[UUID, UUID], Salary] = {}
        self.runs: dict[tuple[UUID, UUID], PayrollRun] = {}
        self.bonuses: dict[tuple[UUID, UUID], PayrollBonus] = {}
        self.payslips: dict[tuple[UUID, UUID], Payslip] = {}
        # Timesheet has no id/run on the wire; store it by its natural key.
        self.timesheets: dict[tuple[UUID, UUID, UUID], Timesheet] = {}
        self.employees: set[tuple[UUID, UUID]] = set()

    # --- configuration ------------------------------------------------------

    async def get_config(self, tenant_id: UUID) -> PayrollConfig | None:
        return self.configs.get(tenant_id)

    async def upsert_config(
        self,
        tenant_id: UUID,
        *,
        currency: str,
        standard_monthly_hours: Decimal,
        overtime_multiplier: Decimal,
        now: datetime,
    ) -> PayrollConfig:
        row = PayrollConfig(
            tenant_id=tenant_id,
            currency=currency,
            standard_monthly_hours=standard_monthly_hours,
            overtime_multiplier=overtime_multiplier,
            updated_at=now,
        )
        self.configs[tenant_id] = row
        return row

    # --- deduction lines ----------------------------------------------------

    async def list_deductions(self, tenant_id: UUID) -> Sequence[PayrollDeduction]:
        rows = [
            row
            for (row_tenant, _), row in self.deductions.items()
            if row_tenant == tenant_id
        ]
        return sorted(rows, key=lambda row: (row.code, str(row.id)))

    async def get_deduction(
        self, tenant_id: UUID, deduction_id: UUID
    ) -> PayrollDeduction | None:
        return self.deductions.get((tenant_id, deduction_id))

    async def deduction_code_taken(
        self, tenant_id: UUID, code: str, exclude_id: UUID | None = None
    ) -> bool:
        return any(
            row.code == code and row.id != exclude_id
            for (row_tenant, _), row in self.deductions.items()
            if row_tenant == tenant_id
        )

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
    ) -> PayrollDeduction:
        row = PayrollDeduction(
            id=deduction_id,
            code=code,
            name=name,
            kind=kind,
            value=value,
            active=active,
            created_at=now,
            updated_at=now,
        )
        self.deductions[(tenant_id, deduction_id)] = row
        return row

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
        key = (tenant_id, deduction_id)
        row = self.deductions.get(key)
        if row is None:
            return None
        changes = {
            field: change
            for field, change in (
                ("name", name),
                ("kind", kind),
                ("value", value),
                ("active", active),
            )
            if change is not None
        }
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.deductions[key] = updated
        return updated

    # --- salary records -----------------------------------------------------

    async def list_salaries(
        self,
        tenant_id: UUID,
        *,
        employee_id: UUID | None,
        page: PageParams,
    ) -> tuple[Sequence[Salary], int]:
        rows = [
            row
            for (row_tenant, _), row in self.salaries.items()
            if row_tenant == tenant_id
            and (employee_id is None or row.employee_id == employee_id)
        ]
        rows.sort(key=lambda row: (row.effective_from, str(row.id)), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

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
        row = Salary(
            id=salary_id,
            employee_id=employee_id,
            amount=amount,
            currency=currency,
            effective_from=effective_from,
            created_at=now,
            created_by=created_by,
        )
        self.salaries[(tenant_id, salary_id)] = row
        return row

    async def salary_effective_date_taken(
        self, tenant_id: UUID, employee_id: UUID, effective_from: date
    ) -> bool:
        return any(
            row.employee_id == employee_id and row.effective_from == effective_from
            for (row_tenant, _), row in self.salaries.items()
            if row_tenant == tenant_id
        )

    async def employee_exists(self, tenant_id: UUID, employee_id: UUID) -> bool:
        return (tenant_id, employee_id) in self.employees

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
        row = PayrollRun(
            id=run_id,
            period=period,
            status=PayrollRunStatus.QUEUED,
            employee_count=0,
            skipped_count=0,
            skipped=[],
            totals=None,
            failed_code=None,
            calculated_at=None,
            finalized_at=None,
            supersedes_run_id=supersedes_run_id,
            superseded_by_run_id=None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.runs[(tenant_id, run_id)] = row
        return row

    async def get_run(self, tenant_id: UUID, run_id: UUID) -> PayrollRun | None:
        return self.runs.get((tenant_id, run_id))

    async def list_runs(
        self, tenant_id: UUID, filters: PayrollRunFilters, page: PageParams
    ) -> tuple[Sequence[PayrollRun], int]:
        rows = [
            row
            for (row_tenant, _), row in self.runs.items()
            if row_tenant == tenant_id
            and (filters.period is None or row.period == filters.period)
            and (filters.status is None or row.status == filters.status)
        ]
        rows.sort(
            key=lambda row: (row.period, row.created_at, str(row.id)),
            reverse=True,
        )
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def open_run_for_period(
        self, tenant_id: UUID, period: str
    ) -> PayrollRun | None:
        open_rows = [
            row
            for (row_tenant, _), row in self.runs.items()
            if row_tenant == tenant_id
            and row.period == period
            and row.status != PayrollRunStatus.FINALIZED
        ]
        return open_rows[0] if open_rows else None

    async def latest_finalized_run(
        self, tenant_id: UUID, period: str
    ) -> PayrollRun | None:
        finalized = [
            row
            for (row_tenant, _), row in self.runs.items()
            if row_tenant == tenant_id
            and row.period == period
            and row.status == PayrollRunStatus.FINALIZED
        ]
        if not finalized:
            return None
        return max(
            finalized,
            key=lambda row: (
                row.finalized_at or row.created_at,
                str(row.id),
            ),
        )

    async def update_run(
        self, tenant_id: UUID, run_id: UUID, **changes: Any
    ) -> PayrollRun | None:
        key = (tenant_id, run_id)
        row = self.runs.get(key)
        if row is None:
            return None
        updated = row.model_copy(update={**changes, "updated_at": UPDATED_AT})
        self.runs[key] = updated
        return updated

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
        key = (tenant_id, run_id)
        row = self.runs.get(key)
        if row is None or row.status not in from_statuses:
            return None
        updated = row.model_copy(
            update={**changes, "status": to_status, "updated_at": now}
        )
        self.runs[key] = updated
        return updated

    # --- bonuses and results ------------------------------------------------

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
    ) -> PayrollBonus:
        row = PayrollBonus(
            id=bonus_id,
            run_id=run_id,
            employee_id=employee_id,
            amount=amount,
            note=note,
            created_by=created_by,
            created_at=now,
        )
        self.bonuses[(tenant_id, bonus_id)] = row
        return row

    async def list_bonuses(
        self, tenant_id: UUID, run_id: UUID
    ) -> Sequence[PayrollBonus]:
        rows = [
            row
            for (row_tenant, _), row in self.bonuses.items()
            if row_tenant == tenant_id and row.run_id == run_id
        ]
        return sorted(rows, key=lambda row: (row.created_at, str(row.id)))

    async def payslip_exists(
        self, tenant_id: UUID, run_id: UUID, employee_id: UUID
    ) -> bool:
        return any(
            row.run_id == run_id and row.employee_id == employee_id
            for (row_tenant, _), row in self.payslips.items()
            if row_tenant == tenant_id
        )

    async def list_run_timesheets(
        self, tenant_id: UUID, run_id: UUID, page: PageParams
    ) -> tuple[Sequence[Timesheet], int]:
        rows = [
            row
            for (row_tenant, stored_run_id, _), row in self.timesheets.items()
            if row_tenant == tenant_id and stored_run_id == run_id
        ]
        rows.sort(key=lambda row: str(row.employee_id))
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def list_run_payslips(
        self, tenant_id: UUID, run_id: UUID, page: PageParams
    ) -> tuple[Sequence[Payslip], int]:
        rows = [
            row
            for (row_tenant, _), row in self.payslips.items()
            if row_tenant == tenant_id and row.run_id == run_id
        ]
        rows.sort(key=lambda row: str(row.employee_id))
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def list_employee_payslips(
        self, tenant_id: UUID, employee_id: UUID, page: PageParams
    ) -> tuple[Sequence[Payslip], int]:
        rows = [
            row
            for (row_tenant, _), row in self.payslips.items()
            if row_tenant == tenant_id and row.employee_id == employee_id
        ]
        rows.sort(key=lambda row: (row.period, str(row.id)), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def get_payslip(self, tenant_id: UUID, payslip_id: UUID) -> Payslip | None:
        return self.payslips.get((tenant_id, payslip_id))


class FakeOvertimeHoursPort:
    """Deterministic overtime seam: verified rows plus the guarded flip."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], list[OvertimeHoursRow]] = {}
        self.included: set[UUID] = set()
        self.mark_calls: list[tuple[UUID, tuple[UUID, ...], UUID]] = []

    def add(
        self,
        tenant_id: UUID,
        period: str,
        *,
        overtime_id: UUID,
        employee_id: UUID,
        hours: Decimal,
    ) -> OvertimeHoursRow:
        row = OvertimeHoursRow(
            overtime_id=overtime_id, employee_id=employee_id, hours=hours
        )
        self.rows.setdefault((tenant_id, period), []).append(row)
        return row

    async def verified_hours(
        self, tenant_id: UUID, period: str
    ) -> Sequence[OvertimeHoursRow]:
        return [
            row
            for row in self.rows.get((tenant_id, period), [])
            if row.overtime_id not in self.included
        ]

    async def mark_included(
        self,
        tenant_id: UUID,
        rows: Sequence[OvertimeHoursRow],
        run_id: UUID,
    ) -> int:
        self.mark_calls.append(
            (tenant_id, tuple(row.overtime_id for row in rows), run_id)
        )
        flipped = 0
        for row in rows:
            if row.overtime_id in self.included:
                continue
            self.included.add(row.overtime_id)
            flipped += 1
        return flipped


class FakeAttendanceHoursPort:
    """Settable attendance facts; every requested employee gets a default 0."""

    def __init__(self) -> None:
        self.worked: dict[tuple[UUID, str, UUID], Decimal] = {}
        self.days: dict[tuple[UUID, str, UUID], int] = {}

    async def worked_hours(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, Decimal]:
        return {
            employee_id: self.worked.get(
                (tenant_id, period, employee_id), Decimal("0.00")
            )
            for employee_id in employee_ids
        }

    async def attendance_days(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, int]:
        return {
            employee_id: self.days.get((tenant_id, period, employee_id), 0)
            for employee_id in employee_ids
        }


class FakeLeaveDaysPort:
    """Settable approved-leave coverage; zero by default."""

    def __init__(self) -> None:
        self.days: dict[tuple[UUID, str, UUID], int] = {}

    async def approved_leave_days(
        self,
        tenant_id: UUID,
        employee_ids: Sequence[UUID],
        period: str,
    ) -> Mapping[UUID, int]:
        return {
            employee_id: self.days.get((tenant_id, period, employee_id), 0)
            for employee_id in employee_ids
        }
