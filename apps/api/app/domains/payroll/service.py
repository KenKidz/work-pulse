"""Use cases for the Payroll domain — the only place its business logic lives.

T5 covers configuration, transparent deduction lines and effective-dated
salary records. Run lifecycle and calculation use cases land in T6/T7.

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

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.payroll.exceptions import (
    EmployeeNotFoundError,
    InvalidPayrollDeductionValueError,
    PayrollDeductionCodeTakenError,
    PayrollDeductionNotFoundError,
    SalaryEffectiveDateTakenError,
)
from app.domains.payroll.policies import (
    ensure_can_configure,
    ensure_can_view_payroll,
)
from app.domains.payroll.repository import PayrollRepository
from app.domains.payroll.schemas import (
    PayrollConfig,
    PayrollConfigUpdate,
    PayrollDeduction,
    PayrollDeductionCreate,
    PayrollDeductionUpdate,
    Salary,
    SalaryCreate,
    SalaryListPage,
    validate_deduction_value,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity
from app.shared.pagination import PageParams

DEFAULT_CURRENCY = "USD"
DEFAULT_STANDARD_MONTHLY_HOURS = Decimal("160.00")
DEFAULT_OVERTIME_MULTIPLIER = Decimal("1.50")

PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent


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
    def __init__(self, repo: PayrollRepository, audit: AuditWriter) -> None:
        self._repo = repo
        self._audit = audit

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

    # --- internals -----------------------------------------------------------

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
