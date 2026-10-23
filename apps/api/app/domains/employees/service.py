"""Use cases for the Employees domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/employees.md`):

- `employee_code` auto-generation per tenant (`EMP-00001`, max suffix + 1) and
  per-tenant uniqueness of email + code → `409 *_taken` (the DB unique
  constraint is the race backstop, mapped in the Postgres repository).
- Assignment refs (department/position/location) must exist in the tenant and
  not be archived on create and on transfer → `transfer_target_archived`
  (409); existence itself is delegated to the Organizations services, so their
  `*_not_found` (404) codes propagate unchanged.
- `manager_id` must be an active employee of the tenant and never the employee
  themself → `employee_manager_invalid` (400) / `employee_self_manager` (400).
- Employment history is append-only: a transfer closes the current half-open
  period at `effective_from` and appends a new open one; nothing is rewritten.
  `effective_from` must be `>= hired_at`, `>= current.effective_from` (else
  `employment_period_overlap`, 409) and `<= today` (no future scheduling in
  Phase 1 → `employee_transfer_effective_invalid`, 422).
- Termination closes the open period, sets `terminated_at`, and nulls
  `manager_id` on every direct report with one audit entry per report
  (PO Decision #4).
- PII never reaches audit `changes` or log lines: `employee.updated` records
  field *names* only, other entries carry ids/state/date values.
- Time is a parameter (`now`) — this module never reads the wall clock.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.employees.events import (
    employee_created,
    employee_terminated,
    employee_transferred,
    employee_updated,
)
from app.domains.employees.exceptions import (
    EmployeeAlreadyTerminatedError,
    EmployeeCodeTakenError,
    EmployeeEmailTakenError,
    EmployeeManagerInvalidError,
    EmployeeNotFoundError,
    EmployeeSelfManagerError,
    EmployeeTransferEffectiveInvalidError,
    EmploymentPeriodOverlapError,
    TransferTargetArchivedError,
)
from app.domains.employees.repository import (
    EmployeeFilters,
    EmployeeRepository,
    EmploymentRepository,
)
from app.domains.employees.schemas import (
    CreateEmployee,
    Employee,
    EmployeeDetail,
    EmployeeListPage,
    EmployeeStatus,
    EmployeeSummary,
    Employment,
    EmploymentList,
    TerminationRequest,
    TransferRequest,
    UpdateEmployee,
)
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity, Role
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams


def _ref(value: UUID | None) -> str | None:
    """Audit-friendly id value (never PII)."""
    return str(value) if value is not None else None


class EmployeeService:
    def __init__(
        self,
        repo: EmployeeRepository,
        employments: EmploymentRepository,
        departments: DepartmentService,
        positions: PositionService,
        locations: LocationService,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._employments = employments
        self._departments = departments
        self._positions = positions
        self._locations = locations
        self._audit = audit
        self._outbox = outbox

    # --- reads ---------------------------------------------------------------

    async def get(self, identity: Identity, employee_id: UUID) -> Employee:
        """Tenant-scoped fetch (no role scoping) — used by mutations."""
        employee = await self._repo.get(identity.tenant_id, employee_id)
        if employee is None:
            raise EmployeeNotFoundError()
        return employee

    async def _get_visible(self, identity: Identity, employee_id: UUID) -> Employee:
        """Fetch with the manager-scoping rule applied.

        A manager may only see their own direct reports; a non-report is a 404
        (never 403 — existence is not leaked). An unlinked manager sees nobody:
        `manager_id=None` would otherwise match every manager-less employee.
        """
        employee = await self.get(identity, employee_id)
        if identity.role == Role.MANAGER and (
            identity.employee_id is None or employee.manager_id != identity.employee_id
        ):
            raise EmployeeNotFoundError()
        return employee

    async def me(self, identity: Identity) -> Employee:
        """`/me`: the employee linked to the current user (`employee_id`)."""
        if identity.employee_id is None:
            raise EmployeeNotFoundError()
        return await self.get(identity, identity.employee_id)

    async def get_detail(self, identity: Identity, employee_id: UUID) -> EmployeeDetail:
        employee = await self._get_visible(identity, employee_id)
        employments = await self._employments.history(identity.tenant_id, employee_id)
        return EmployeeDetail(
            **employee.model_dump(),
            current_employment=self._current_of(employments),
            employments=list(employments),
        )

    async def list_employments(
        self, identity: Identity, employee_id: UUID
    ) -> EmploymentList:
        await self._get_visible(identity, employee_id)
        return list(await self._employments.history(identity.tenant_id, employee_id))

    async def list(
        self,
        identity: Identity,
        filters: EmployeeFilters,
        page: PageParams,
    ) -> EmployeeListPage:
        effective = filters
        if identity.role == Role.MANAGER:
            # Server-side scoping: a manager always lists their own reports only.
            if identity.employee_id is None:
                # No employee link → no reports. (`manager_id=None` would mean
                # "unscoped" in the repository and leak every employee.)
                return EmployeeListPage(items=[], page=page.make_page(0))
            effective = filters.model_copy(update={"manager_id": identity.employee_id})
        items, total = await self._repo.list(identity.tenant_id, effective, page)
        return EmployeeListPage(
            items=[EmployeeSummary.model_validate(item) for item in items],
            page=page.make_page(total),
        )

    # --- writes --------------------------------------------------------------

    async def create(
        self, identity: Identity, payload: CreateEmployee, now: datetime
    ) -> Employee:
        tenant_id = identity.tenant_id
        if await self._repo.email_taken(tenant_id, payload.email):
            raise EmployeeEmailTakenError()
        if payload.employee_code is not None and await self._repo.code_taken(
            tenant_id, payload.employee_code
        ):
            raise EmployeeCodeTakenError()

        await self._ensure_targets(
            identity, payload.department_id, payload.position_id, payload.location_id
        )

        employee_id = uuid7()
        if payload.manager_id is not None:
            await self._ensure_manager(identity, employee_id, payload.manager_id)

        employee_code = payload.employee_code or await self._repo.next_employee_code(
            tenant_id
        )
        created = await self._repo.add(
            tenant_id,
            employee_id,
            employee_code=employee_code,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email,
            phone=payload.phone,
            department_id=payload.department_id,
            position_id=payload.position_id,
            location_id=payload.location_id,
            manager_id=payload.manager_id,
            employment_type=payload.employment_type,
            status=EmployeeStatus.ACTIVE,
            hired_at=payload.hired_at,
        )
        await self._employments.add(
            tenant_id,
            uuid7(),
            employee_id=created.id,
            department_id=payload.department_id,
            position_id=payload.position_id,
            location_id=payload.location_id,
            employment_type=payload.employment_type,
            effective_from=payload.hired_at,
        )
        # One transaction (ADR 0012): the row, its employment period, its
        # audit entry and this envelope commit together — or not at all.
        await self._outbox.append(
            employee_created(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                employee_id=created.id,
                department_id=created.department_id,
                position_id=created.position_id,
                location_id=created.location_id,
                employment_type=created.employment_type.value,
                hired_at=created.hired_at,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="employee.created",
                entity_type="employee",
                entity_id=created.id,
                occurred_at=now,
                changes={
                    "employee_code": {"old": None, "new": created.employee_code},
                    "department_id": {"old": None, "new": str(created.department_id)},
                    "position_id": {"old": None, "new": str(created.position_id)},
                    "location_id": {"old": None, "new": str(created.location_id)},
                    "manager_id": {"old": None, "new": _ref(created.manager_id)},
                    "employment_type": {
                        "old": None,
                        "new": created.employment_type.value,
                    },
                },
            )
        )
        return created

    async def update(
        self,
        identity: Identity,
        employee_id: UUID,
        payload: UpdateEmployee,
        now: datetime,
    ) -> Employee:
        tenant_id = identity.tenant_id
        existing = await self.get(identity, employee_id)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        if "email" in changes and changes["email"] != existing.email:
            if await self._repo.email_taken(
                tenant_id, changes["email"], exclude_id=employee_id
            ):
                raise EmployeeEmailTakenError()

        updated = await self._repo.update(tenant_id, employee_id, **changes)
        if updated is None:  # pragma: no cover — lost race between get and update
            raise EmployeeNotFoundError()

        # Append before the audit write (ADR 0012): a mutation whose event
        # cannot be stored fails loudly and leaves nothing behind.
        await self._outbox.append(
            employee_updated(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                employee_id=employee_id,
                # Field names only, never the values (PII rule).
                changed_fields=list(changes),
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="employee.updated",
                entity_type="employee",
                entity_id=employee_id,
                occurred_at=now,
                # PII rule: field names only, never the values.
                changes={field: "changed" for field in changes},
            )
        )
        return updated

    async def transfer(
        self,
        identity: Identity,
        employee_id: UUID,
        payload: TransferRequest,
        now: datetime,
    ) -> Employment:
        tenant_id = identity.tenant_id
        employee = await self.get(identity, employee_id)
        if employee.status == EmployeeStatus.TERMINATED:
            raise EmployeeAlreadyTerminatedError()

        effective_from = payload.effective_from
        if effective_from < employee.hired_at or effective_from > now.date():
            raise EmployeeTransferEffectiveInvalidError()

        current = await self._employments.current(tenant_id, employee_id)
        if current is None:  # pragma: no cover — active employees always have one
            raise EmployeeTransferEffectiveInvalidError()
        if effective_from < current.effective_from:
            # Backdating would overlap the closed period (pinned code).
            raise EmploymentPeriodOverlapError()

        department_id = payload.department_id
        position_id = current.position_id
        if (
            "position_id" in payload.model_fields_set
            and payload.position_id is not None
        ):
            position_id = payload.position_id
        location_id = current.location_id
        if (
            "location_id" in payload.model_fields_set
            and payload.location_id is not None
        ):
            location_id = payload.location_id
        await self._ensure_targets(identity, department_id, position_id, location_id)

        manager_changed = "manager_id" in payload.model_fields_set
        new_manager_id = payload.manager_id if manager_changed else employee.manager_id
        if manager_changed and new_manager_id is not None:
            await self._ensure_manager(identity, employee_id, new_manager_id)

        closed = await self._employments.close(tenant_id, current.id, effective_from)
        if closed is None:
            # Concurrent close: the open period changed under us — never
            # rewrite a closed period (DB guard + partial unique index).
            raise EmploymentPeriodOverlapError()
        period = await self._employments.add(
            tenant_id,
            uuid7(),
            employee_id=employee_id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            employment_type=current.employment_type,
            effective_from=effective_from,
        )
        ref_changes: dict[str, Any] = {
            "department_id": {
                "old": _ref(employee.department_id),
                "new": _ref(department_id),
            },
            "effective_from": {"old": None, "new": effective_from.isoformat()},
        }
        if position_id != employee.position_id:
            ref_changes["position_id"] = {
                "old": _ref(employee.position_id),
                "new": _ref(position_id),
            }
        if location_id != employee.location_id:
            ref_changes["location_id"] = {
                "old": _ref(employee.location_id),
                "new": _ref(location_id),
            }
        updated = await self._repo.update(
            tenant_id,
            employee_id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            **({"manager_id": new_manager_id} if manager_changed else {}),
        )
        if updated is None:  # pragma: no cover — lost race
            raise EmployeeNotFoundError()
        if manager_changed:
            ref_changes["manager_id"] = {
                "old": _ref(employee.manager_id),
                "new": _ref(new_manager_id),
            }

        # Append before the audit write (ADR 0012): `from` is the closed
        # assignment, `to` the newly opened one.
        await self._outbox.append(
            employee_transferred(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                employee_id=employee_id,
                from_department_id=employee.department_id,
                to_department_id=department_id,
                effective_from=effective_from,
                to_position_id=position_id,
                to_location_id=location_id,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="employee.transferred",
                entity_type="employee",
                entity_id=employee_id,
                occurred_at=now,
                reason=payload.reason,
                changes=ref_changes,
            )
        )
        return period

    async def terminate(
        self,
        identity: Identity,
        employee_id: UUID,
        payload: TerminationRequest,
        now: datetime,
    ) -> Employee:
        tenant_id = identity.tenant_id
        employee = await self.get(identity, employee_id)
        if employee.status == EmployeeStatus.TERMINATED:
            raise EmployeeAlreadyTerminatedError()

        current = await self._employments.current(tenant_id, employee_id)
        if current is not None and payload.effective_date < current.effective_from:
            # Closing before the open period starts would break the invariant.
            raise EmploymentPeriodOverlapError()

        updated = await self._repo.update(
            tenant_id,
            employee_id,
            status=EmployeeStatus.TERMINATED,
            terminated_at=payload.effective_date,
        )
        if updated is None:  # pragma: no cover — lost race between get and update
            raise EmployeeNotFoundError()
        if current is not None:
            closed = await self._employments.close(
                tenant_id, current.id, payload.effective_date
            )
            if closed is None:
                # Concurrent close/termination — never rewrite a closed period.
                raise EmploymentPeriodOverlapError()

        # Decision #4: terminating a manager unassigns their direct reports.
        cleared_report_ids = await self._repo.clear_manager(tenant_id, employee_id)

        # Append before the audit write (ADR 0012). The `manager_cleared`
        # audit entries below emit nothing — not in the event catalog.
        await self._outbox.append(
            employee_terminated(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                employee_id=employee_id,
                effective_date=payload.effective_date,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="employee.terminated",
                entity_type="employee",
                entity_id=employee_id,
                occurred_at=now,
                reason=payload.reason,  # reason lives on the audit entry only
                changes={
                    "status": {
                        "old": employee.status.value,
                        "new": EmployeeStatus.TERMINATED.value,
                    },
                    "effective_date": {
                        "old": None,
                        "new": payload.effective_date.isoformat(),
                    },
                },
            )
        )
        for report_id in cleared_report_ids:
            await self._audit.write(
                AuditEntryCreate(
                    tenant_id=tenant_id,
                    actor_id=identity.actor_id,
                    action="employee.manager_cleared",
                    entity_type="employee",
                    entity_id=report_id,
                    occurred_at=now,
                    changes={"manager_id": {"old": _ref(employee_id), "new": None}},
                )
            )
        return updated

    # --- shared checks ---------------------------------------------------------

    async def _ensure_targets(
        self,
        identity: Identity,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
    ) -> None:
        """Refs must exist in the tenant (org 404s propagate) and be live."""
        department = await self._departments.get(identity, department_id)
        if department.archived_at is not None:
            raise TransferTargetArchivedError()
        position = await self._positions.get(identity, position_id)
        if position.archived_at is not None:
            raise TransferTargetArchivedError()
        location = await self._locations.get(identity, location_id)
        if location.archived_at is not None:
            raise TransferTargetArchivedError()

    async def _ensure_manager(
        self, identity: Identity, employee_id: UUID, manager_id: UUID
    ) -> None:
        if manager_id == employee_id:
            raise EmployeeSelfManagerError()
        manager = await self._repo.get(identity.tenant_id, manager_id)
        if manager is None or manager.status != EmployeeStatus.ACTIVE:
            raise EmployeeManagerInvalidError()

    @staticmethod
    def _current_of(employments: Sequence[Employment]) -> Employment | None:
        """The open period, if any (`effective_to IS NULL`)."""
        return next(
            (period for period in employments if period.effective_to is None), None
        )
