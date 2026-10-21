"""Postgres repository for the Payroll domain (T5 + the T3 protocol).

Every statement carries `WHERE tenant_id = :tenant` first — tenant scoping
lives here and nowhere else (`.ai/security.md`). The adapter speaks the pinned
wire schemas (`domains/payroll/schemas.py`); ORM rows never cross the domain
boundary.

Race safety:

- `salary_records` and `payroll_deduction_lines` unique violations are mapped
  to their domain `409` errors (the DB constraint is the backstop behind the
  service pre-check).
- `payroll_runs`' partial unique index (`status <> 'finalized'`) is the
  one-open-run-per-period guarantee; a losing insert becomes
  `payroll_period_open`.
- `transition_run` is a conditional `UPDATE … WHERE status IN (…)`, which is
  the exactly-one-wins primitive for finalize and the worker's claim (R6).

Money columns are read as `Decimal` (ADR 0017) and amounts are PII: this
module never logs or interpolates them (`.ai/security.md`).
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees import models as employees_orm
from app.domains.payroll import models as orm
from app.domains.payroll.exceptions import (
    PayrollDeductionCodeTakenError,
    PayrollPeriodOpenError,
    SalaryEffectiveDateTakenError,
)
from app.domains.payroll.repository import PayrollRunFilters
from app.domains.payroll.schemas import (
    PayrollBonus,
    PayrollConfig,
    PayrollDeduction,
    PayrollDeductionKind,
    PayrollRun,
    PayrollRunSkip,
    PayrollRunStatus,
    PayrollRunTotals,
    Payslip,
    PayslipDeductionLine,
    PayslipStatus,
    Salary,
    Timesheet,
)
from app.shared.pagination import PageParams


def _unique_violation(exc: IntegrityError) -> str:
    """The violated constraint/index name (asyncpg), with a message fallback."""
    return getattr(exc.orig, "constraint_name", None) or str(exc.orig)


def _run(row: orm.PayrollRun) -> PayrollRun:
    """ORM run row → the pinned wire shape (totals are flattened in storage)."""
    totals: PayrollRunTotals | None = None
    base = row.totals_base
    overtime_pay = row.totals_overtime_pay
    bonuses = row.totals_bonuses
    deductions = row.totals_deductions
    net = row.totals_net
    if (
        base is not None
        and overtime_pay is not None
        and bonuses is not None
        and deductions is not None
        and net is not None
    ):
        totals = PayrollRunTotals(
            base=base,
            overtime_pay=overtime_pay,
            bonuses=bonuses,
            deductions=deductions,
            net=net,
        )
    return PayrollRun(
        id=row.id,
        period=row.period,
        status=PayrollRunStatus(row.status),
        employee_count=row.employee_count,
        skipped_count=row.skipped_count,
        skipped=[PayrollRunSkip.model_validate(item) for item in row.skipped or []],
        totals=totals,
        failed_code=row.failed_code,
        calculated_at=row.calculated_at,
        finalized_at=row.finalized_at,
        supersedes_run_id=row.supersedes_run_id,
        superseded_by_run_id=row.superseded_by_run_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_values(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Translate wire-shaped run changes into ORM column values."""
    values: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "totals":
            totals = value
            if totals is None:
                values.update(
                    {
                        "totals_base": None,
                        "totals_overtime_pay": None,
                        "totals_bonuses": None,
                        "totals_deductions": None,
                        "totals_net": None,
                    }
                )
                continue
            if isinstance(totals, Mapping):
                totals = PayrollRunTotals.model_validate(totals)
            values.update(
                {
                    "totals_base": totals.base,
                    "totals_overtime_pay": totals.overtime_pay,
                    "totals_bonuses": totals.bonuses,
                    "totals_deductions": totals.deductions,
                    "totals_net": totals.net,
                }
            )
        elif key == "skipped":
            values["skipped"] = [
                PayrollRunSkip.model_validate(item).model_dump(mode="json")
                for item in value
            ]
        elif key == "status":
            values["status"] = (
                value.value if isinstance(value, PayrollRunStatus) else value
            )
        else:
            values[key] = value
    return values


class PostgresPayrollRepository:
    """Tenant-scoped persistence for payroll config, inputs and results."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- configuration ------------------------------------------------------

    async def get_config(self, tenant_id: UUID) -> PayrollConfig | None:
        stmt = select(orm.PayrollConfig).where(orm.PayrollConfig.tenant_id == tenant_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return PayrollConfig.model_validate(row) if row is not None else None

    async def create_config_if_absent(
        self,
        tenant_id: UUID,
        *,
        currency: str,
        standard_monthly_hours: Decimal,
        overtime_multiplier: Decimal,
        now: datetime,
    ) -> PayrollConfig:
        """Provision defaults without overwriting a concurrently saved config.

        `ON CONFLICT DO NOTHING` waits for the competing writer, then the
        re-read returns its committed row — defaults never win over a real
        administrator update (the first-read/first-salary race).
        """
        stmt = (
            pg_insert(orm.PayrollConfig)
            .values(
                id=uuid7(),
                tenant_id=tenant_id,
                currency=currency,
                standard_monthly_hours=standard_monthly_hours,
                overtime_multiplier=overtime_multiplier,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id"])
        )
        await self._session.execute(stmt)
        config = await self.get_config(tenant_id)
        if config is None:  # pragma: no cover — the insert or the winner wrote it
            raise RuntimeError("payroll config provisioning did not return a row")
        return config

    async def update_config_fields(
        self,
        tenant_id: UUID,
        *,
        currency: str | None = None,
        standard_monthly_hours: Decimal | None = None,
        overtime_multiplier: Decimal | None = None,
        now: datetime,
    ) -> PayrollConfig | None:
        """Patch only the supplied columns, so disjoint partial updates merge."""
        values: dict[str, Any] = {"updated_at": now}
        if currency is not None:
            values["currency"] = currency
        if standard_monthly_hours is not None:
            values["standard_monthly_hours"] = standard_monthly_hours
        if overtime_multiplier is not None:
            values["overtime_multiplier"] = overtime_multiplier
        stmt = (
            update(orm.PayrollConfig)
            .where(orm.PayrollConfig.tenant_id == tenant_id)
            .values(**values)
            .returning(orm.PayrollConfig.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_config(tenant_id)

    # --- deduction lines ----------------------------------------------------

    async def list_deductions(self, tenant_id: UUID) -> Sequence[PayrollDeduction]:
        stmt = (
            select(orm.PayrollDeductionLine)
            .where(orm.PayrollDeductionLine.tenant_id == tenant_id)
            .order_by(
                orm.PayrollDeductionLine.code.asc(),
                orm.PayrollDeductionLine.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [PayrollDeduction.model_validate(row) for row in rows]

    async def get_deduction(
        self, tenant_id: UUID, deduction_id: UUID
    ) -> PayrollDeduction | None:
        stmt = select(orm.PayrollDeductionLine).where(
            orm.PayrollDeductionLine.tenant_id == tenant_id,
            orm.PayrollDeductionLine.id == deduction_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return PayrollDeduction.model_validate(row) if row is not None else None

    async def deduction_code_taken(
        self, tenant_id: UUID, code: str, exclude_id: UUID | None = None
    ) -> bool:
        stmt = select(orm.PayrollDeductionLine.id).where(
            orm.PayrollDeductionLine.tenant_id == tenant_id,
            orm.PayrollDeductionLine.code == code,
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.PayrollDeductionLine.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

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
        row = orm.PayrollDeductionLine(
            id=deduction_id,
            tenant_id=tenant_id,
            code=code,
            name=name,
            kind=kind.value,
            value=value,
            active=active,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Race backstop for (tenant_id, code) — the service pre-checks.
            if "uq_payroll_deduction_lines_tenant_code" in _unique_violation(exc):
                raise PayrollDeductionCodeTakenError() from exc
            raise
        await self._session.refresh(row)
        return PayrollDeduction.model_validate(row)

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
        stmt = select(orm.PayrollDeductionLine).where(
            orm.PayrollDeductionLine.tenant_id == tenant_id,
            orm.PayrollDeductionLine.id == deduction_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        if name is not None:
            row.name = name
        if kind is not None:
            row.kind = kind.value
        if value is not None:
            row.value = value
        if active is not None:
            row.active = active
        row.updated_at = now
        await self._session.flush()
        await self._session.refresh(row)
        return PayrollDeduction.model_validate(row)

    # --- salary records -----------------------------------------------------

    async def list_salaries(
        self,
        tenant_id: UUID,
        *,
        employee_id: UUID | None,
        page: PageParams,
    ) -> tuple[Sequence[Salary], int]:
        conditions = [orm.SalaryRecord.tenant_id == tenant_id]
        if employee_id is not None:
            conditions.append(orm.SalaryRecord.employee_id == employee_id)
        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.SalaryRecord).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(orm.SalaryRecord)
            .where(*conditions)
            .order_by(
                orm.SalaryRecord.effective_from.desc(),
                orm.SalaryRecord.created_at.desc(),
                orm.SalaryRecord.id.desc(),
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [Salary.model_validate(row) for row in rows], total

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
        row = orm.SalaryRecord(
            id=salary_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            amount=amount,
            currency=currency,
            effective_from=effective_from,
            created_by=created_by,
            created_at=now,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Race backstop for (tenant, employee, effective_from).
            if "uq_salary_records_tenant_employee_effective" in _unique_violation(exc):
                raise SalaryEffectiveDateTakenError() from exc
            raise
        await self._session.refresh(row)
        return Salary.model_validate(row)

    async def salary_effective_date_taken(
        self, tenant_id: UUID, employee_id: UUID, effective_from: date
    ) -> bool:
        stmt = select(orm.SalaryRecord.id).where(
            orm.SalaryRecord.tenant_id == tenant_id,
            orm.SalaryRecord.employee_id == employee_id,
            orm.SalaryRecord.effective_from == effective_from,
        )
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def employee_exists(self, tenant_id: UUID, employee_id: UUID) -> bool:
        stmt = select(employees_orm.Employee.id).where(
            employees_orm.Employee.tenant_id == tenant_id,
            employees_orm.Employee.id == employee_id,
        )
        return (await self._session.execute(stmt.limit(1))).first() is not None

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
        row = orm.PayrollRun(
            id=run_id,
            tenant_id=tenant_id,
            period=period,
            status=PayrollRunStatus.QUEUED.value,
            employee_count=0,
            skipped_count=0,
            skipped=[],
            created_by=created_by,
            supersedes_run_id=supersedes_run_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Partial unique index: one non-finalized run per (tenant, period).
            if "uq_payroll_runs_open_period" in _unique_violation(exc):
                raise PayrollPeriodOpenError() from exc
            raise
        await self._session.refresh(row)
        return _run(row)

    async def get_run(self, tenant_id: UUID, run_id: UUID) -> PayrollRun | None:
        stmt = select(orm.PayrollRun).where(
            orm.PayrollRun.tenant_id == tenant_id,
            orm.PayrollRun.id == run_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _run(row) if row is not None else None

    async def list_runs(
        self, tenant_id: UUID, filters: PayrollRunFilters, page: PageParams
    ) -> tuple[Sequence[PayrollRun], int]:
        conditions = [orm.PayrollRun.tenant_id == tenant_id]
        if filters.period is not None:
            conditions.append(orm.PayrollRun.period == filters.period)
        if filters.status is not None:
            conditions.append(orm.PayrollRun.status == filters.status.value)
        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.PayrollRun).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(orm.PayrollRun)
            .where(*conditions)
            .order_by(
                orm.PayrollRun.period.desc(),
                orm.PayrollRun.created_at.desc(),
                orm.PayrollRun.id.desc(),
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_run(row) for row in rows], total

    async def open_run_for_period(
        self, tenant_id: UUID, period: str
    ) -> PayrollRun | None:
        stmt = (
            select(orm.PayrollRun)
            .where(
                orm.PayrollRun.tenant_id == tenant_id,
                orm.PayrollRun.period == period,
                orm.PayrollRun.status != PayrollRunStatus.FINALIZED.value,
            )
            .order_by(orm.PayrollRun.created_at.asc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _run(row) if row is not None else None

    async def latest_finalized_run(
        self, tenant_id: UUID, period: str
    ) -> PayrollRun | None:
        stmt = (
            select(orm.PayrollRun)
            .where(
                orm.PayrollRun.tenant_id == tenant_id,
                orm.PayrollRun.period == period,
                orm.PayrollRun.status == PayrollRunStatus.FINALIZED.value,
            )
            .order_by(
                orm.PayrollRun.finalized_at.desc(),
                orm.PayrollRun.created_at.desc(),
                orm.PayrollRun.id.desc(),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _run(row) if row is not None else None

    async def update_run(
        self, tenant_id: UUID, run_id: UUID, **changes: Any
    ) -> PayrollRun | None:
        stmt = select(orm.PayrollRun).where(
            orm.PayrollRun.tenant_id == tenant_id,
            orm.PayrollRun.id == run_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in _run_values(changes).items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return _run(row)

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
        values = _run_values(changes)
        values.update({"status": to_status.value, "updated_at": now})
        stmt = (
            select(orm.PayrollRun)
            .where(
                orm.PayrollRun.tenant_id == tenant_id,
                orm.PayrollRun.id == run_id,
                orm.PayrollRun.status.in_([status.value for status in from_statuses]),
            )
            .with_for_update()
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in values.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return _run(row)

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
    ) -> PayrollBonus:
        row = orm.PayrollBonus(
            id=bonus_id,
            tenant_id=tenant_id,
            run_id=run_id,
            employee_id=employee_id,
            amount=amount,
            note=note,
            created_by=created_by,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return PayrollBonus.model_validate(row)

    async def list_bonuses(
        self, tenant_id: UUID, run_id: UUID
    ) -> Sequence[PayrollBonus]:
        stmt = (
            select(orm.PayrollBonus)
            .where(
                orm.PayrollBonus.tenant_id == tenant_id,
                orm.PayrollBonus.run_id == run_id,
            )
            .order_by(
                orm.PayrollBonus.created_at.asc(),
                orm.PayrollBonus.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [PayrollBonus.model_validate(row) for row in rows]

    async def payslip_exists(
        self, tenant_id: UUID, run_id: UUID, employee_id: UUID
    ) -> bool:
        stmt = select(orm.Payslip.id).where(
            orm.Payslip.tenant_id == tenant_id,
            orm.Payslip.run_id == run_id,
            orm.Payslip.employee_id == employee_id,
        )
        return (await self._session.execute(stmt.limit(1))).first() is not None

    # --- results ------------------------------------------------------------

    async def list_run_timesheets(
        self, tenant_id: UUID, run_id: UUID, page: PageParams
    ) -> tuple[Sequence[Timesheet], int]:
        conditions = [
            orm.PayrollTimesheet.tenant_id == tenant_id,
            orm.PayrollTimesheet.run_id == run_id,
        ]
        total = (
            await self._session.execute(
                select(func.count())
                .select_from(orm.PayrollTimesheet)
                .where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(orm.PayrollTimesheet)
            .where(*conditions)
            .order_by(orm.PayrollTimesheet.employee_id.asc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [Timesheet.model_validate(row) for row in rows], total

    async def list_run_payslips(
        self, tenant_id: UUID, run_id: UUID, page: PageParams
    ) -> tuple[Sequence[Payslip], int]:
        conditions = [
            orm.Payslip.tenant_id == tenant_id,
            orm.Payslip.run_id == run_id,
        ]
        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.Payslip).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(orm.Payslip)
            .where(*conditions)
            .order_by(orm.Payslip.employee_id.asc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = list((await self._session.execute(stmt)).scalars())
        return await self._with_lines(tenant_id, rows), total

    async def list_employee_payslips(
        self, tenant_id: UUID, employee_id: UUID, page: PageParams
    ) -> tuple[Sequence[Payslip], int]:
        conditions = [
            orm.Payslip.tenant_id == tenant_id,
            orm.Payslip.employee_id == employee_id,
        ]
        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.Payslip).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(orm.Payslip)
            .where(*conditions)
            .order_by(
                orm.Payslip.period.desc(),
                orm.Payslip.created_at.desc(),
                orm.Payslip.id.desc(),
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = list((await self._session.execute(stmt)).scalars())
        return await self._with_lines(tenant_id, rows), total

    async def get_payslip(self, tenant_id: UUID, payslip_id: UUID) -> Payslip | None:
        stmt = select(orm.Payslip).where(
            orm.Payslip.tenant_id == tenant_id,
            orm.Payslip.id == payslip_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        payslips = await self._with_lines(tenant_id, [row])
        return payslips[0]

    # --- internals ----------------------------------------------------------

    async def _with_lines(
        self, tenant_id: UUID, rows: Sequence[orm.Payslip]
    ) -> list[Payslip]:
        """Attach each payslip's ordered deduction lines in one extra query."""
        if not rows:
            return []
        payslip_ids = [row.id for row in rows]
        stmt = (
            select(orm.PayslipDeductionLine)
            .where(
                orm.PayslipDeductionLine.tenant_id == tenant_id,
                orm.PayslipDeductionLine.payslip_id.in_(payslip_ids),
            )
            .order_by(
                orm.PayslipDeductionLine.payslip_id.asc(),
                orm.PayslipDeductionLine.code.asc(),
                orm.PayslipDeductionLine.id.asc(),
            )
        )
        lines = (await self._session.execute(stmt)).scalars()
        by_payslip: dict[UUID, list[orm.PayslipDeductionLine]] = {
            payslip_id: [] for payslip_id in payslip_ids
        }
        for line in lines:
            by_payslip[line.payslip_id].append(line)

        return [
            Payslip(
                id=row.id,
                run_id=row.run_id,
                employee_id=row.employee_id,
                period=row.period,
                status=PayslipStatus(row.status),
                base=row.base,
                overtime_pay=row.overtime_pay,
                bonuses=row.bonuses,
                deductions=[
                    PayslipDeductionLine(
                        code=line.code,
                        name=line.name,
                        kind=PayrollDeductionKind(line.kind),
                        value=line.value,
                        amount=line.amount,
                    )
                    for line in by_payslip[row.id]
                ],
                net=row.net,
                issued_at=row.issued_at,
            )
            for row in rows
        ]
