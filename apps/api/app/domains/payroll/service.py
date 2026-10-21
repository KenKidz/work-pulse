"""Use cases for the Payroll domain — the only place its business logic lives.

T5 covers configuration, transparent deduction lines and effective-dated
salary records. T6 adds the run lifecycle (create, recalculate, finalize,
bonuses) and T7 the asynchronous calculation job. The event contracts are the
producers' pinned shapes (ADR 0012/0017/0018).

Rules implemented here (pinned in `.ai/features/payroll.md`):

- **Defaults.** A tenant without a config row gets one provisioned with
  `USD`, `160.00` standard monthly hours and `1.50` overtime multiplier
  (the spec's defaults), idempotently on first read or salary write.
  `currency` is one currency per tenant and is stamped onto salary records at
  creation.
- **Roles.** Config, deduction lines and salary records are written by
  Finance/Admin/Super Admin (`ensure_can_configure` → `403`); HR reads them
  (`ensure_can_view_payroll`); employee/manager have no payroll standing and
  receive a `404` (`payroll_not_found`), never a hint that data exists.
- **Deduction rules.** The value rule is kind-dependent (`0.01..100` for
  `percent_of_base`, `>= 0.01` for `fixed_amount`). A PATCH may supply one side
  of the (kind, value) pair, so the service validates the merged rule and
  answers `422` when it is invalid. `code` is immutable and unique per tenant.
- **Salary history is append-only.** One record per `(employee, effective_from)`
  (`409 salary_effective_date_taken`); the target employee must exist in the
  tenant (`404 employee_not_found`). Future effective dates are allowed — they
  simply do not participate in a closed month's calculation.
- **Audit.** Every mutation writes one entry. Amounts (salary amounts and
  deduction values) are PII-like and recorded as "changed", never with their
  value (`.ai/security.md`). Configuration numbers are not PII, so their
  old/new values are recorded as strings.
- **Clock.** Every mutation takes `now`; the router is the only wall-clock
  reader and tests inject a fixed instant.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.payroll.calculation import (
    CalculatedPayslip,
    EmployeePayrollInput,
    SalaryPoint,
    calculate_run_inputs,
    period_end,
    period_start,
)
from app.domains.payroll.events import payroll_calculated, payslip_issued
from app.domains.payroll.exceptions import (
    EmployeeNotFoundError,
    InvalidPayrollDeductionValueError,
    PayrollDeductionCodeTakenError,
    PayrollDeductionNotFoundError,
    PayrollEmployeeNotInRunError,
    PayrollPeriodNotClosedError,
    PayrollPeriodOpenError,
    PayrollRunFinalizedError,
    PayrollRunNotCalculatedError,
    PayrollRunNotFoundError,
    PayrollRunStaleError,
    SalaryEffectiveDateTakenError,
)
from app.domains.payroll.policies import (
    ensure_can_configure,
    ensure_can_run,
    ensure_can_view_payroll,
)
from app.domains.payroll.repository import (
    AttendanceHoursPort,
    LeaveDaysPort,
    NoAttendanceHoursPort,
    NoLeaveDaysPort,
    NoOvertimeHoursPort,
    OvertimeHoursPort,
    OvertimeHoursRow,
    PayrollRepository,
    RunTimesheet,
)
from app.domains.payroll.schemas import (
    BonusCreate,
    PayrollBonus,
    PayrollConfig,
    PayrollConfigUpdate,
    PayrollDeduction,
    PayrollDeductionCreate,
    PayrollDeductionUpdate,
    PayrollRun,
    PayrollRunCreate,
    PayrollRunSkip,
    PayrollRunStatus,
    PayrollRunTotals,
    Salary,
    SalaryCreate,
    SalaryListPage,
    Timesheet,
    validate_deduction_value,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

DEFAULT_CURRENCY = "USD"
DEFAULT_STANDARD_MONTHLY_HOURS = Decimal("160.00")
DEFAULT_OVERTIME_MULTIPLIER = Decimal("1.50")

PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent
CALCULATION_FAILED_CODE = "payroll_calculation_failed"


@dataclass(frozen=True, slots=True)
class _PreparedCalculation:
    """A pure calculation pass ready to be persisted (no ids, no statuses)."""

    payslips: list[CalculatedPayslip]
    totals: PayrollRunTotals
    skipped: list[PayrollRunSkip]
    timesheets: list[RunTimesheet]


def _audit_value(value: Any) -> Any:
    """Make an audit old/new value JSON-safe (amounts are never passed here)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class PayrollService:
    def __init__(
        self,
        repo: PayrollRepository,
        audit: AuditWriter,
        outbox: EventOutbox | None = None,
        overtime: OvertimeHoursPort | None = None,
        attendance: AttendanceHoursPort | None = None,
        leave_days: LeaveDaysPort | None = None,
    ) -> None:
        self._repo = repo
        self._audit = audit
        # T5's input-management use cases emit no events; T6/T7's lifecycle
        # transitions require the transactional outbox (ADR 0012).
        self._outbox = outbox
        self._overtime = overtime or NoOvertimeHoursPort()
        self._attendance = attendance or NoAttendanceHoursPort()
        self._leave_days = leave_days or NoLeaveDaysPort()

    # --- configuration -------------------------------------------------------

    async def get_config(self, identity: Identity, now: datetime) -> PayrollConfig:
        """The tenant's config, provisioned with defaults on first read."""
        ensure_can_view_payroll(identity)
        return await self._config(identity.tenant_id, now)

    async def update_config(
        self, identity: Identity, payload: PayrollConfigUpdate, now: datetime
    ) -> PayrollConfig:
        """Partial update by Finance/Admin/Super Admin; every change is audited."""
        ensure_can_configure(identity)
        tenant_id = identity.tenant_id
        current = await self._config(tenant_id, now)

        merged = {
            "currency": payload.currency
            if payload.currency is not None
            else current.currency,
            "standard_monthly_hours": payload.standard_monthly_hours
            if payload.standard_monthly_hours is not None
            else current.standard_monthly_hours,
            "overtime_multiplier": payload.overtime_multiplier
            if payload.overtime_multiplier is not None
            else current.overtime_multiplier,
        }
        changes: dict[str, Any] = {}
        for field, new_value in merged.items():
            old_value = getattr(current, field)
            if old_value != new_value:
                changes[field] = {
                    "old": _audit_value(old_value),
                    "new": _audit_value(new_value),
                }
        if not changes:
            return current

        updated = await self._repo.update_config_fields(
            tenant_id,
            currency=payload.currency,
            standard_monthly_hours=payload.standard_monthly_hours,
            overtime_multiplier=payload.overtime_multiplier,
            now=now,
        )
        if updated is None:  # pragma: no cover — `_config` just ensured the row
            raise RuntimeError("payroll config disappeared during update")
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.config_updated",
                entity_type="payroll_config",
                # One config row per tenant; its wire shape carries no id.
                entity_id=tenant_id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    # --- deduction lines -----------------------------------------------------

    async def list_deductions(self, identity: Identity) -> Sequence[PayrollDeduction]:
        """All tenant rules, ordered by `code` for deterministic payslip lines."""
        ensure_can_view_payroll(identity)
        return await self._repo.list_deductions(identity.tenant_id)

    async def create_deduction(
        self,
        identity: Identity,
        payload: PayrollDeductionCreate,
        now: datetime,
    ) -> PayrollDeduction:
        """Add one transparent deduction rule (code unique per tenant)."""
        ensure_can_configure(identity)
        tenant_id = identity.tenant_id
        if await self._repo.deduction_code_taken(tenant_id, payload.code):
            raise PayrollDeductionCodeTakenError()

        deduction = await self._repo.add_deduction(
            tenant_id,
            uuid7(),
            code=payload.code,
            name=payload.name,
            kind=payload.kind,
            value=payload.value,
            active=payload.active,
            now=now,
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.deduction_created",
                entity_type="payroll_deduction",
                entity_id=deduction.id,
                occurred_at=now,
                changes={
                    "code": {"old": None, "new": deduction.code},
                    "name": {"old": None, "new": deduction.name},
                    "kind": {"old": None, "new": deduction.kind.value},
                    # A deduction value is an amount-like rule: never audited.
                    "value": PII_CHANGED,
                    "active": {"old": None, "new": deduction.active},
                },
            )
        )
        return deduction

    async def update_deduction(
        self,
        identity: Identity,
        deduction_id: UUID,
        payload: PayrollDeductionUpdate,
        now: datetime,
    ) -> PayrollDeduction:
        """Patch a rule; `code` is immutable and the merged rule must be valid."""
        ensure_can_configure(identity)
        tenant_id = identity.tenant_id
        current = await self._repo.get_deduction(tenant_id, deduction_id)
        if current is None:
            raise PayrollDeductionNotFoundError()

        merged_kind = payload.kind if payload.kind is not None else current.kind
        merged_value = payload.value if payload.value is not None else current.value
        try:
            validate_deduction_value(merged_kind, merged_value)
        except ValueError:
            raise InvalidPayrollDeductionValueError() from None

        changes: dict[str, Any] = {}
        if payload.name is not None and payload.name != current.name:
            changes["name"] = {
                "old": current.name,
                "new": payload.name,
            }
        if payload.kind is not None and payload.kind is not current.kind:
            changes["kind"] = {
                "old": current.kind.value,
                "new": payload.kind.value,
            }
        if payload.value is not None and payload.value != current.value:
            changes["value"] = PII_CHANGED
        if payload.active is not None and payload.active != current.active:
            changes["active"] = {
                "old": current.active,
                "new": payload.active,
            }
        if not changes:
            return current

        updated = await self._repo.update_deduction(
            tenant_id,
            deduction_id,
            name=payload.name,
            kind=payload.kind,
            value=payload.value,
            active=payload.active,
            now=now,
        )
        if updated is None:  # pragma: no cover — fetched above in the same tx
            raise PayrollDeductionNotFoundError()
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.deduction_updated",
                entity_type="payroll_deduction",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    # --- salary records ------------------------------------------------------

    async def list_salaries(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None,
        page: PageParams,
    ) -> SalaryListPage:
        """Paged append-only salary history, newest `effective_from` first."""
        ensure_can_view_payroll(identity)
        items, total = await self._repo.list_salaries(
            identity.tenant_id, employee_id=employee_id, page=page
        )
        return SalaryListPage(items=list(items), page=page.make_page(total))

    async def create_salary(
        self, identity: Identity, payload: SalaryCreate, now: datetime
    ) -> Salary:
        """Append a salary record; the tenant config supplies its currency."""
        ensure_can_configure(identity)
        tenant_id = identity.tenant_id

        if not await self._repo.employee_exists(tenant_id, payload.employee_id):
            raise EmployeeNotFoundError()
        if await self._repo.salary_effective_date_taken(
            tenant_id, payload.employee_id, payload.effective_from
        ):
            raise SalaryEffectiveDateTakenError()

        config = await self._config(tenant_id, now)
        salary = await self._repo.add_salary(
            tenant_id,
            uuid7(),
            employee_id=payload.employee_id,
            amount=payload.amount,
            currency=config.currency,
            effective_from=payload.effective_from,
            created_by=identity.actor_id,
            now=now,
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.salary_created",
                entity_type="salary_record",
                entity_id=salary.id,
                occurred_at=now,
                changes={
                    "employee_id": {
                        "old": None,
                        "new": str(salary.employee_id),
                    },
                    # Salary amounts are PII: the value never reaches audit.
                    "amount": PII_CHANGED,
                    "currency": {"old": None, "new": salary.currency},
                    "effective_from": {
                        "old": None,
                        "new": salary.effective_from.isoformat(),
                    },
                },
            )
        )
        return salary

    # --- run lifecycle (T6) --------------------------------------------------

    async def create_run(
        self, identity: Identity, payload: PayrollRunCreate, now: datetime
    ) -> PayrollRun:
        """Queue a run for a closed month; a correction run supersedes on finalize."""
        ensure_can_run(identity)
        tenant_id = identity.tenant_id
        period = payload.period

        # "A job over a closed period" (ADR 0003): the last calendar day must
        # be strictly before today, so the month cannot be in progress.
        if period_end(period) >= now.date():
            raise PayrollPeriodNotClosedError()
        if await self._repo.open_run_for_period(tenant_id, period) is not None:
            raise PayrollPeriodOpenError()

        prior = await self._repo.latest_finalized_run(tenant_id, period)
        run = await self._repo.add_run(
            tenant_id,
            uuid7(),
            period=period,
            created_by=identity.actor_id,
            supersedes_run_id=prior.id if prior is not None else None,
            now=now,
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.run_created",
                entity_type="payroll_run",
                entity_id=run.id,
                occurred_at=now,
                changes={
                    "period": {"old": None, "new": run.period},
                    "status": {"old": None, "new": run.status.value},
                    "supersedes_run_id": {
                        "old": None,
                        "new": str(run.supersedes_run_id)
                        if run.supersedes_run_id is not None
                        else None,
                    },
                },
            )
        )
        return run

    async def recalculate_run(
        self, identity: Identity, run_id: UUID, now: datetime
    ) -> PayrollRun:
        """Re-queue a calculated/failed run; finalized is immutable."""
        ensure_can_run(identity)
        tenant_id = identity.tenant_id
        run = await self._repo.get_run(tenant_id, run_id)
        if run is None:
            raise PayrollRunNotFoundError()
        if run.status is PayrollRunStatus.FINALIZED:
            raise PayrollRunFinalizedError()
        if run.status is PayrollRunStatus.QUEUED:
            # Already queued: idempotent, no duplicate work or audit noise.
            return run
        if run.status is PayrollRunStatus.CALCULATING:
            raise PayrollRunNotCalculatedError()

        # `calculated_at` is deliberately retained: it marks that this run has
        # already emitted its one `payroll.calculated` event. The spec says a
        # recalculation emits nothing (`.ai/features/payroll.md` → Events).
        updated = await self._repo.transition_run(
            tenant_id,
            run_id,
            from_statuses=(PayrollRunStatus.CALCULATED, PayrollRunStatus.FAILED),
            to_status=PayrollRunStatus.QUEUED,
            now=now,
            failed_code=None,
            totals=None,
            employee_count=0,
            skipped_count=0,
            skipped=[],
        )
        if updated is None:
            current = await self._repo.get_run(tenant_id, run_id)
            if current is not None and current.status is PayrollRunStatus.QUEUED:
                return current
            if current is not None and current.status is PayrollRunStatus.FINALIZED:
                raise PayrollRunFinalizedError()
            raise PayrollRunNotCalculatedError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.run_recalculated",
                entity_type="payroll_run",
                entity_id=run.id,
                occurred_at=now,
                changes={
                    "status": {"old": run.status.value, "new": updated.status.value},
                    "failed_code": {"old": run.failed_code, "new": None},
                },
            )
        )
        return updated

    async def finalize_run(
        self, identity: Identity, run_id: UUID, now: datetime
    ) -> PayrollRun:
        """Issue payslips, flip counted overtime and supersede a prior run.

        The conditional transition is the exactly-one-wins race guard; the
        payslip flips, overtime write and outbox appends commit with it
        (ADR 0018 decision 3).
        """
        ensure_can_run(identity)
        tenant_id = identity.tenant_id
        run = await self._repo.get_run(tenant_id, run_id)
        if run is None:
            raise PayrollRunNotFoundError()
        if run.status is PayrollRunStatus.FINALIZED:
            raise PayrollRunFinalizedError()
        if run.status is not PayrollRunStatus.CALCULATED:
            raise PayrollRunNotCalculatedError()

        finalized = await self._repo.transition_run(
            tenant_id,
            run_id,
            from_statuses=(PayrollRunStatus.CALCULATED,),
            to_status=PayrollRunStatus.FINALIZED,
            now=now,
            finalized_at=now,
            failed_code=None,
        )
        if finalized is None:
            # A concurrent finalize won: exactly one transition (AC5).
            raise PayrollRunNotCalculatedError()

        # Flip exactly the overtime rows the calculation consumed. A short
        # count means an input changed after calculation (ADR 0018).
        overtime_rows = await self._repo.list_run_overtime_rows(tenant_id, run_id)
        if overtime_rows:
            flipped = await self._overtime.mark_included(
                tenant_id, overtime_rows, run_id
            )
            if flipped != len(overtime_rows):
                raise PayrollRunStaleError()

        issued = await self._repo.mark_payslips_issued(tenant_id, run_id, now)
        if len(issued) != finalized.employee_count:
            # A calculated run's payslips are exactly the counted employees;
            # a mismatch means the result set is stale.
            raise PayrollRunStaleError()
        outbox = self._require_outbox()
        await outbox.append_many(
            [
                payslip_issued(
                    tenant_id=tenant_id,
                    actor_id=identity.actor_id,
                    occurred_at=now,
                    payslip_id=issued_ref.payslip_id,
                    run_id=run_id,
                    employee_id=issued_ref.employee_id,
                    period=finalized.period,
                )
                for issued_ref in issued
            ]
        )

        if finalized.supersedes_run_id is not None:
            await self._supersede_prior_run(
                tenant_id,
                prior_run_id=finalized.supersedes_run_id,
                new_run_id=run_id,
                identity=identity,
                now=now,
            )

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.run_finalized",
                entity_type="payroll_run",
                entity_id=finalized.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": PayrollRunStatus.CALCULATED.value,
                        "new": finalized.status.value,
                    },
                },
            )
        )
        return finalized

    async def create_bonus(
        self, identity: Identity, run_id: UUID, payload: BonusCreate, now: datetime
    ) -> PayrollBonus:
        """Add a per-run bonus for an employee already in the calculated run."""
        ensure_can_run(identity)
        tenant_id = identity.tenant_id
        # Lock before checking status: a concurrent finalize must not commit
        # between the check and the insert (terminal immutability).
        run = await self._repo.lock_run(tenant_id, run_id)
        if run is None:
            raise PayrollRunNotFoundError()
        if run.status is PayrollRunStatus.FINALIZED:
            raise PayrollRunFinalizedError()
        if not await self._repo.payslip_exists(tenant_id, run_id, payload.employee_id):
            raise PayrollEmployeeNotInRunError()

        bonus = await self._repo.add_bonus(
            tenant_id,
            uuid7(),
            run_id=run_id,
            employee_id=payload.employee_id,
            amount=payload.amount,
            note=payload.note,
            created_by=identity.actor_id,
            now=now,
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.bonus_created",
                entity_type="payroll_bonus",
                entity_id=bonus.id,
                occurred_at=now,
                changes={
                    "employee_id": {
                        "old": None,
                        "new": str(bonus.employee_id),
                    },
                    # Bonus amounts and notes are PII (`.ai/security.md`).
                    "amount": PII_CHANGED,
                    "note": PII_CHANGED if bonus.note is not None else None,
                },
            )
        )
        return bonus

    # --- calculation job (T7) ------------------------------------------------

    async def calculate_run(
        self,
        run_id: UUID,
        now: datetime,
        *,
        tenant_id: UUID | None = None,
    ) -> PayrollRun:
        """Drive one run through the asynchronous calculation job.

        `QUEUED → CALCULATING → CALCULATED | FAILED`. The worker has no request
        identity, so when `tenant_id` is omitted the run's tenant is resolved
        with the repository's worker-only lookup. Already-calculated,
        finalized and failed runs are no-ops, and a duplicate pass that loses
        the claim cannot replace the result set (the repository re-checks the
        run is still `CALCULATING`). A failed calculation is recoverable
        through `recalculate`; a recalculation emits no run-level event.
        """
        tenant = tenant_id or await self._repo.tenant_id_for_run(run_id)
        if tenant is None:
            raise PayrollRunNotFoundError()
        run = await self._repo.get_run(tenant, run_id)
        if run is None:
            raise PayrollRunNotFoundError()
        if run.status in (
            PayrollRunStatus.CALCULATED,
            PayrollRunStatus.FINALIZED,
            PayrollRunStatus.FAILED,
        ):
            return run

        if run.status is PayrollRunStatus.QUEUED:
            claimed = await self._repo.transition_run(
                tenant,
                run_id,
                from_statuses=(PayrollRunStatus.QUEUED,),
                to_status=PayrollRunStatus.CALCULATING,
                now=now,
                failed_code=None,
            )
            if claimed is None:
                current = await self._repo.get_run(tenant, run_id)
                if current is None:
                    raise PayrollRunNotFoundError()
                if current.status in (
                    PayrollRunStatus.CALCULATED,
                    PayrollRunStatus.FINALIZED,
                    PayrollRunStatus.FAILED,
                ):
                    return current
                run = current
            else:
                run = claimed

        # A retained `calculated_at` means this run already emitted its one
        # `payroll.calculated` event; a recalculation must not emit again.
        was_calculated = run.calculated_at is not None
        try:
            prepared = await self._prepare_calculation(tenant, run, now)
            replaced = await self._repo.replace_run_results(
                tenant,
                run_id,
                timesheets=prepared.timesheets,
                payslips=prepared.payslips,
                now=now,
            )
        except Exception:
            # A calculation failure is a persisted state, not a request error:
            # the run stays recalculable and exposes a stable, PII-free code.
            # `replace_run_results` is savepoint-guarded, so a failed write
            # leaves the previous provisional set intact.
            return await self._fail_run(tenant, run_id, now)

        if not replaced:
            # A duplicate pass lost the claim: the run already moved on
            # (calculated/finalized), so its result set must not be replaced.
            current = await self._repo.get_run(tenant, run_id)
            if current is None:
                raise PayrollRunNotFoundError()
            return current

        calculated = await self._repo.transition_run(
            tenant,
            run_id,
            from_statuses=(PayrollRunStatus.CALCULATING,),
            to_status=PayrollRunStatus.CALCULATED,
            now=now,
            employee_count=len(prepared.payslips),
            skipped_count=len(prepared.skipped),
            skipped=prepared.skipped,
            totals=prepared.totals,
            calculated_at=now,
            failed_code=None,
        )
        if calculated is None:
            current = await self._repo.get_run(tenant, run_id)
            if current is None:
                raise PayrollRunNotFoundError()
            if current.status in (
                PayrollRunStatus.CALCULATED,
                PayrollRunStatus.FINALIZED,
            ):
                return current
            raise PayrollRunStaleError()

        if not was_calculated:
            await self._require_outbox().append(
                payroll_calculated(
                    tenant_id=tenant,
                    actor_id=calculated.created_by,
                    occurred_at=now,
                    run_id=calculated.id,
                    period=calculated.period,
                    employee_count=calculated.employee_count,
                )
            )
        return calculated

    # --- internals -----------------------------------------------------------

    async def _prepare_calculation(
        self, tenant_id: UUID, run: PayrollRun, now: datetime
    ) -> "_PreparedCalculation":
        """Assemble every input, then run the pure deterministic calculator."""
        config = await self._config(tenant_id, now)
        start = period_start(run.period)
        end = period_end(run.period)

        employee_ids = list(
            await self._repo.list_employees_in_scope(tenant_id, start, end)
        )
        salaries = await self._repo.list_salary_history(tenant_id, employee_ids)
        bonuses = await self._repo.list_bonuses(tenant_id, run.id)
        deductions = await self._repo.list_deductions(tenant_id)

        scope = set(employee_ids)
        overtime_rows = [
            row
            for row in await self._overtime.verified_hours(tenant_id, run.period)
            if row.employee_id in scope
        ]
        overtime_by_employee: dict[UUID, list[OvertimeHoursRow]] = defaultdict(list)
        for row in overtime_rows:
            overtime_by_employee[row.employee_id].append(row)

        worked_hours = await self._attendance.worked_hours(
            tenant_id, employee_ids, run.period
        )
        attendance_days = await self._attendance.attendance_days(
            tenant_id, employee_ids, run.period
        )
        leave_days = await self._leave_days.approved_leave_days(
            tenant_id, employee_ids, run.period
        )

        salary_by_employee: dict[UUID, list[SalaryPoint]] = defaultdict(list)
        for record in salaries:
            salary_by_employee[record.employee_id].append(
                SalaryPoint(
                    amount=record.amount,
                    effective_from=record.effective_from,
                )
            )
        bonus_by_employee: dict[UUID, list[Decimal]] = defaultdict(list)
        for bonus in bonuses:
            bonus_by_employee[bonus.employee_id].append(bonus.amount)

        inputs = [
            EmployeePayrollInput(
                employee_id=employee_id,
                salaries=sorted(
                    salary_by_employee.get(employee_id, ()),
                    key=lambda point: (point.effective_from, point.amount),
                ),
                overtime_hours=sum(
                    (row.hours for row in overtime_by_employee.get(employee_id, ())),
                    Decimal("0"),
                ),
                bonuses=bonus_by_employee.get(employee_id, ()),
            )
            for employee_id in employee_ids
        ]
        result = calculate_run_inputs(
            period=run.period,
            employees=inputs,
            deductions=deductions,
            standard_monthly_hours=config.standard_monthly_hours,
            overtime_multiplier=config.overtime_multiplier,
        )

        # Timesheets are informational and assembled for every in-scope
        # employee. Only a paid employee's overtime handles are persisted: a
        # `no_salary` skip has no payslip, so its verified rows must stay
        # available for a later run (no silent inclusion).
        paid_ids = {payslip.employee_id for payslip in result.payslips}
        timesheets: list[RunTimesheet] = []
        for employee_id in sorted(employee_ids, key=str):
            rows = overtime_by_employee.get(employee_id, ())
            overtime_hours = sum((row.hours for row in rows), Decimal("0"))
            timesheets.append(
                RunTimesheet(
                    timesheet=Timesheet(
                        employee_id=employee_id,
                        period=run.period,
                        worked_hours=worked_hours.get(employee_id, Decimal("0.00")),
                        overtime_hours=overtime_hours.quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        ),
                        leave_days=leave_days.get(employee_id, 0),
                        attendance_days=attendance_days.get(employee_id, 0),
                    ),
                    overtime_rows=tuple(rows) if employee_id in paid_ids else (),
                )
            )

        return _PreparedCalculation(
            payslips=result.payslips,
            totals=result.totals,
            skipped=result.skipped,
            timesheets=timesheets,
        )

    async def _fail_run(
        self, tenant_id: UUID, run_id: UUID, now: datetime
    ) -> PayrollRun:
        failed = await self._repo.transition_run(
            tenant_id,
            run_id,
            from_statuses=(PayrollRunStatus.CALCULATING,),
            to_status=PayrollRunStatus.FAILED,
            now=now,
            failed_code=CALCULATION_FAILED_CODE,
        )
        if failed is not None:
            return failed
        current = await self._repo.get_run(tenant_id, run_id)
        if current is None:
            raise PayrollRunNotFoundError()
        return current

    async def _supersede_prior_run(
        self,
        tenant_id: UUID,
        *,
        prior_run_id: UUID,
        new_run_id: UUID,
        identity: Identity,
        now: datetime,
    ) -> None:
        prior = await self._repo.get_run(tenant_id, prior_run_id)
        if prior is None or prior.status is not PayrollRunStatus.FINALIZED:
            return
        await self._repo.supersede_payslips(tenant_id, prior_run_id, now)
        await self._repo.update_run(
            tenant_id,
            prior_run_id,
            superseded_by_run_id=new_run_id,
            updated_at=now,
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="payroll.run_superseded",
                entity_type="payroll_run",
                entity_id=prior_run_id,
                occurred_at=now,
                changes={
                    "superseded_by_run_id": {
                        "old": None,
                        "new": str(new_run_id),
                    }
                },
            )
        )

    def _require_outbox(self) -> EventOutbox:
        if self._outbox is None:  # pragma: no cover — composition root wires it
            raise RuntimeError("payroll service requires an event outbox")
        return self._outbox

    async def _config(self, tenant_id: UUID, now: datetime) -> PayrollConfig:
        """Fetch the tenant config, provisioning the pinned defaults once.

        Provisioning is insert-if-absent, never an upsert: a GET or salary
        write racing an administrator's first config PUT must not overwrite the
        saved values with defaults.
        """
        config = await self._repo.get_config(tenant_id)
        if config is not None:
            return config
        return await self._repo.create_config_if_absent(
            tenant_id,
            currency=DEFAULT_CURRENCY,
            standard_monthly_hours=DEFAULT_STANDARD_MONTHLY_HOURS,
            overtime_multiplier=DEFAULT_OVERTIME_MULTIPLIER,
            now=now,
        )
