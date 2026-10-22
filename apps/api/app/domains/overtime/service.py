"""Use cases for the Overtime domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/overtime.md`):

- **State machine**: `REQUESTED → MANAGER_APPROVED → HR_VERIFIED →
  INCLUDED_IN_PAYROLL`, with `REJECTED` reachable from every pre-inclusion state
  and `WITHDRAWN` from `REQUESTED`/`MANAGER_APPROVED`. `REJECTED`, `WITHDRAWN`
  and `INCLUDED_IN_PAYROLL` are terminal. Every transition except payroll's
  inclusion flip is an event + an audit entry appended in the mutation's
  transaction (ADR 0012); `INCLUDED_IN_PAYROLL` is payroll's accounting flip
  through the ADR 0018 write port, never an overtime operation.
- **Own-only claiming**: the request's employee is always
  `identity.employee_id`; a user without an employee link or a non-active
  employee cannot submit (`404`/`409 employee_not_active`). One workday plus
  decimal hours per request (`0 < hours ≤ 12`), and `work_date ≤ today`
  (server date at submit, taken from the injected clock).
- **Open-date overlap**: a new claim on the same `work_date` as an own
  `requested`/`manager_approved`/`hr_verified` claim is `409 overtime_overlaps`;
  rejected/withdrawn/included rows never block (counted rows are separate
  claims and each pays its own verified hours).
- **Never self-decision**: the requester can never decide their own request
  (`403`), whatever their role. Scope: a manager decides only direct reports
  (`employee_facts.manager_id` / `visible_employee_ids`); hr/admin/super_admin
  are tenant-wide; out-of-scope requests are `404` (existence is not leaked).
- **Two-step decision**: `approve` is the manager step (manager/HR/Admin/Super
  Admin); `verify` is the HR step (HR/Admin/Super Admin only). Reject uses the
  manager-step gate from `requested` and the HR-step gate from
  `manager_approved`/`hr_verified`; it records the observed `rejected_from`.
  HR accepts or rejects the requested hours exactly (PO Decision 3).
- **Withdrawal is own-only and pre-verification**: from `requested` or
  `manager_approved`; `hr_verified` is payroll-bound and terminal rows cannot
  be withdrawn (PO Decision 4).
- **Clock**: every mutation takes `now` as its last parameter — the router is
  the only wall-clock reader, and tests inject a fixed instant.
- **PII**: `reason`/`manager_note`/`hr_note` never reach logs, events or audit
  values; audit records only a `"changed"` marker for those fields.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus
from app.domains.overtime.events import (
    overtime_approved,
    overtime_rejected,
    overtime_requested,
    overtime_verified,
    overtime_withdrawn,
)
from app.domains.overtime.exceptions import (
    EmployeeNotActiveError,
    EmployeeNotFoundError,
    OvertimeNotManagerApprovedError,
    OvertimeNotOpenError,
    OvertimeNotRequestedError,
    OvertimeOverlapsError,
    OvertimeRequestNotFoundError,
    OvertimeWorkDateInFutureError,
)
from app.domains.overtime.policies import ensure_can_approve, ensure_can_verify
from app.domains.overtime.repository import (
    OvertimeRepository,
    OvertimeRequestFilters,
)
from app.domains.overtime.schemas import (
    OvertimeDecision,
    OvertimeRejectedFrom,
    OvertimeRequest,
    OvertimeRequestCreate,
    OvertimeRequestListPage,
    OvertimeRequestStatus,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent
_WITHDRAWABLE = (
    OvertimeRequestStatus.REQUESTED,
    OvertimeRequestStatus.MANAGER_APPROVED,
)


class OvertimeService:
    def __init__(
        self,
        repo: OvertimeRepository,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._outbox = outbox

    # --- mutations -----------------------------------------------------------

    async def create_request(
        self, identity: Identity, payload: OvertimeRequestCreate, now: datetime
    ) -> OvertimeRequest:
        """Submit a claim for the actor's employee link — always `REQUESTED`."""
        tenant_id = identity.tenant_id
        employee_id = identity.employee_id
        if employee_id is None:
            raise EmployeeNotFoundError()
        facts = await self._repo.employee_facts(tenant_id, employee_id)
        if facts is None:
            raise EmployeeNotFoundError()
        if facts.status != EmployeeStatus.ACTIVE:
            raise EmployeeNotActiveError()

        # A claim is for hours already worked (PO Decision 2).
        if payload.work_date > now.date():
            raise OvertimeWorkDateInFutureError()
        # Serialize submits per employee while the overlap check and insert run:
        # two concurrent submits must not both pass `find_open_overlap`.
        if not await self._repo.lock_employee(tenant_id, employee_id):
            raise EmployeeNotFoundError()
        if await self._repo.find_open_overlap(
            tenant_id, employee_id, payload.work_date
        ):
            raise OvertimeOverlapsError()

        request = await self._repo.create_request(
            tenant_id,
            uuid7(),
            employee_id=employee_id,
            work_date=payload.work_date,
            hours=payload.hours,
            reason=payload.reason,
        )

        await self._outbox.append(
            overtime_requested(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                overtime_id=request.id,
                employee_id=request.employee_id,
                work_date=request.work_date,
                hours=request.hours,
            )
        )
        changes: dict[str, Any] = {
            "employee_id": {"old": None, "new": str(request.employee_id)},
            "work_date": {"old": None, "new": request.work_date.isoformat()},
            "hours": {"old": None, "new": str(request.hours)},
            "status": {"old": None, "new": request.status.value},
        }
        if request.reason is not None:
            changes["reason"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="overtime.requested",
                entity_type="overtime_request",
                entity_id=request.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return request

    async def approve(
        self,
        identity: Identity,
        request_id: UUID,
        payload: OvertimeDecision,
        now: datetime,
    ) -> OvertimeRequest:
        """Manager step: `requested → manager_approved`."""
        request = await self._decidable(identity, request_id)
        ensure_can_approve(identity)
        if request.status is not OvertimeRequestStatus.REQUESTED:
            raise OvertimeNotRequestedError()

        tenant_id = identity.tenant_id
        updated = await self._repo.transition_approve(
            tenant_id,
            request_id,
            approved_by=identity.actor_id,
            now=now,
            manager_note=payload.note,
        )
        if updated is None:
            # A concurrent decider already won — never double-apply.
            raise OvertimeNotRequestedError()

        await self._outbox.append(
            overtime_approved(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                overtime_id=updated.id,
                employee_id=updated.employee_id,
                work_date=updated.work_date,
                hours=updated.hours,
                approved_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": OvertimeRequestStatus.REQUESTED.value,
                "new": OvertimeRequestStatus.MANAGER_APPROVED.value,
            },
            "approved_by": {"old": None, "new": str(identity.actor_id)},
        }
        if payload.note is not None:
            changes["manager_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="overtime.approved",
                entity_type="overtime_request",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def verify(
        self,
        identity: Identity,
        request_id: UUID,
        payload: OvertimeDecision,
        now: datetime,
    ) -> OvertimeRequest:
        """HR step: `manager_approved → hr_verified`, hours unchanged."""
        request = await self._decidable(identity, request_id)
        ensure_can_verify(identity)
        if request.status is not OvertimeRequestStatus.MANAGER_APPROVED:
            raise OvertimeNotManagerApprovedError()

        tenant_id = identity.tenant_id
        updated = await self._repo.transition_verify(
            tenant_id,
            request_id,
            verified_by=identity.actor_id,
            now=now,
            hr_note=payload.note,
        )
        if updated is None:
            raise OvertimeNotManagerApprovedError()

        await self._outbox.append(
            overtime_verified(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                overtime_id=updated.id,
                employee_id=updated.employee_id,
                work_date=updated.work_date,
                hours=updated.hours,
                verified_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": OvertimeRequestStatus.MANAGER_APPROVED.value,
                "new": OvertimeRequestStatus.HR_VERIFIED.value,
            },
            "verified_by": {"old": None, "new": str(identity.actor_id)},
        }
        if payload.note is not None:
            changes["hr_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="overtime.verified",
                entity_type="overtime_request",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def reject(
        self,
        identity: Identity,
        request_id: UUID,
        payload: OvertimeDecision,
        now: datetime,
    ) -> OvertimeRequest:
        """Reject from any open state, recording the observed `rejected_from`.

        The note routes by step: `manager_note` from `requested` (manager-step
        roles) and `hr_note` from `manager_approved`/`hr_verified` (HR-step
        roles). Rejecting an `hr_verified` row is the ADR 0018 un-verify window
        (PO Decision 6); inclusion and other terminal states are `not_open`.
        """
        request = await self._decidable(identity, request_id)
        match request.status:
            case OvertimeRequestStatus.REQUESTED:
                ensure_can_approve(identity)
                rejected_from = OvertimeRejectedFrom.REQUESTED
                manager_note, hr_note = payload.note, None
            case OvertimeRequestStatus.MANAGER_APPROVED:
                ensure_can_verify(identity)
                rejected_from = OvertimeRejectedFrom.MANAGER_APPROVED
                manager_note, hr_note = None, payload.note
            case OvertimeRequestStatus.HR_VERIFIED:
                ensure_can_verify(identity)
                rejected_from = OvertimeRejectedFrom.HR_VERIFIED
                manager_note, hr_note = None, payload.note
            case _:
                # No step owns a terminal row. A non-decider still fails the
                # role gate first (fail closed); a decider gets the pinned
                # terminal-state refusal.
                ensure_can_approve(identity)
                raise OvertimeNotOpenError()

        tenant_id = identity.tenant_id
        updated = await self._repo.transition_reject(
            tenant_id,
            request_id,
            from_status=rejected_from,
            rejected_by=identity.actor_id,
            now=now,
            manager_note=manager_note,
            hr_note=hr_note,
        )
        if updated is None:
            # The observed state was lost to a concurrent decision.
            raise OvertimeNotOpenError()

        await self._outbox.append(
            overtime_rejected(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                overtime_id=updated.id,
                employee_id=updated.employee_id,
                work_date=updated.work_date,
                hours=updated.hours,
                rejected_by=identity.actor_id,
                rejected_from=rejected_from.value,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": rejected_from.value,
                "new": OvertimeRequestStatus.REJECTED.value,
            },
            "rejected_by": {"old": None, "new": str(identity.actor_id)},
            "rejected_from": {"old": None, "new": rejected_from.value},
        }
        if manager_note is not None:
            changes["manager_note"] = PII_CHANGED  # PII: never the value
        if hr_note is not None:
            changes["hr_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="overtime.rejected",
                entity_type="overtime_request",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def withdraw(
        self, identity: Identity, request_id: UUID, now: datetime
    ) -> OvertimeRequest:
        """Withdraw the actor's own open pre-verification request."""
        tenant_id = identity.tenant_id
        employee_id = identity.employee_id
        request = await self._repo.get_request(tenant_id, request_id)
        if request is None:
            raise OvertimeRequestNotFoundError()
        if employee_id is None or request.employee_id != employee_id:
            # Visible standing but not the owner is a forbidden transition; an
            # out-of-scope viewer must not learn the request exists.
            if not await self._visible(identity, request.employee_id):
                raise OvertimeRequestNotFoundError()
            raise ForbiddenError("Only the requester can withdraw an overtime request")
        if request.status not in _WITHDRAWABLE:
            raise OvertimeNotOpenError()

        updated = await self._repo.transition_withdraw(
            tenant_id,
            request_id,
            from_status=request.status,
            employee_id=employee_id,
            now=now,
        )
        if updated is None:
            # A concurrent decision already moved the row out of an open state.
            raise OvertimeNotOpenError()

        await self._outbox.append(
            overtime_withdrawn(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                overtime_id=updated.id,
                employee_id=updated.employee_id,
                work_date=updated.work_date,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="overtime.withdrawn",
                entity_type="overtime_request",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": request.status.value,
                        "new": OvertimeRequestStatus.WITHDRAWN.value,
                    }
                },
            )
        )
        return updated

    # --- reads ---------------------------------------------------------------

    async def list_requests(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None = None,
        status: OvertimeRequestStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        page: PageParams,
    ) -> OvertimeRequestListPage:
        """Paged requests scoped to the caller's visibility (## Roles & access)."""
        employee_ids = await self._list_scope(identity, employee_id)
        filters = OvertimeRequestFilters(
            employee_ids=employee_ids,
            status=status,
            from_date=from_date,
            to_date=to_date,
        )
        items, total = await self._repo.list_requests(identity.tenant_id, filters, page)
        return OvertimeRequestListPage(items=list(items), page=page.make_page(total))

    async def get_request(
        self, identity: Identity, request_id: UUID
    ) -> OvertimeRequest:
        """Request detail; unknown, cross-tenant and out-of-scope are all `404`."""
        request = await self._repo.get_request(identity.tenant_id, request_id)
        if request is None or not await self._visible(identity, request.employee_id):
            raise OvertimeRequestNotFoundError()
        return request

    # --- internals -----------------------------------------------------------

    async def _decidable(self, identity: Identity, request_id: UUID) -> OvertimeRequest:
        """Fetch a request the caller may decide; never leaks out-of-scope ids."""
        request = await self._repo.get_request(identity.tenant_id, request_id)
        if request is None:
            raise OvertimeRequestNotFoundError()
        if (
            identity.employee_id is not None
            and request.employee_id == identity.employee_id
        ):
            raise ForbiddenError(
                "An overtime request cannot be decided by its requester"
            )
        if identity.role == Role.MANAGER and not await self._visible(
            identity, request.employee_id
        ):
            raise OvertimeRequestNotFoundError()
        return request

    async def _visible(self, identity: Identity, employee_id: UUID) -> bool:
        """Whether the identity may see this employee's overtime requests."""
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            return (
                identity.employee_id is not None and employee_id == identity.employee_id
            )
        if identity.role == Role.MANAGER:
            if identity.employee_id is None:
                return False
            # One source for the manager matrix: the same self + direct-reports
            # scope `_list_scope` uses, so detail and list can never disagree.
            visible = await self._repo.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
            return employee_id in visible
        return True  # hr / admin / super_admin

    async def _list_scope(
        self, identity: Identity, employee_id: UUID | None
    ) -> Sequence[UUID] | None:
        """Effective employee scope; `None` means unscoped (hr/admin/super_admin).

        An explicit `employee_id` outside the caller's visibility, or unknown to
        the tenant, is a `404 employee_not_found` — existence is not leaked.
        """
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            if identity.employee_id is None:
                if employee_id is not None:
                    raise EmployeeNotFoundError()
                return []
            if employee_id is not None and employee_id != identity.employee_id:
                raise EmployeeNotFoundError()
            return [identity.employee_id]
        if identity.role == Role.MANAGER:
            visible = await self._repo.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
            if employee_id is not None:
                if employee_id not in visible:
                    raise EmployeeNotFoundError()
                return [employee_id]
            return visible
        if employee_id is not None:
            # Tenant-wide viewer: the filter target must still exist in this
            # tenant (pinned query semantics, T1).
            if await self._repo.employee_facts(identity.tenant_id, employee_id) is None:
                raise EmployeeNotFoundError()
            return [employee_id]
        return None
