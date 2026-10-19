"""Use cases for the Leave domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/leave.md`):

- **State machine**: `PENDING → APPROVED | REJECTED | WITHDRAWN`; the three
  targets are terminal. Every transition is an event + an audit entry, appended
  to the transactional outbox and the audit log in the mutation's transaction
  (ADR 0012).
- **Own-only requesting**: the request's employee is always
  `identity.employee_id`; a user without an employee link or a non-active
  employee cannot submit (`404`/`409 employee_not_active`).
- **Never self-approval**: the requester can never approve or reject their own
  request (`403`), whatever their role. A manager can decide only direct
  reports (`employee_facts.manager_id`); hr/admin/super_admin decide tenant-wide;
  out-of-scope requests are `404` (existence is not leaked).
- **Whole inclusive days**: `days = (end_date - start_date) + 1`.
  `annual`/`unpaid` must start today or later; `sick` may start up to 7 days in
  the past. Anything older is `409 leave_start_too_old`.
- **Overlaps**: a new range intersecting an own `PENDING`/`APPROVED` request
  (inclusive) is `409 leave_overlaps`; rejected/withdrawn ranges never block.
- **Balances**: per `(tenant, employee, type, year of start_date)`.
  `annual`/`sick` are checked at submit and re-checked at approve (early check
  plus the repository's race-safe `used_days + :days <= entitlement_days`
  UPDATE); `unpaid` never touches a balance. A balance never goes negative.
- **Visibility**: own for `employee`/`finance`, self + direct reports for
  `manager`, all for `hr`/`admin`/`super_admin` (## Roles & access).
- **Clock**: every mutation takes `now` as its last parameter — the router is
  the only wall-clock reader, and tests inject a fixed instant.
- **PII**: `reason`/`decision_note` never reach logs, events or audit values;
  audit records only a `"changed"` marker for those fields.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.employees.schemas import EmployeeStatus
from app.domains.leave.events import (
    leave_approved,
    leave_rejected,
    leave_requested,
    leave_withdrawn,
)
from app.domains.leave.exceptions import (
    EmployeeNotActiveError,
    LeaveInsufficientBalanceError,
    LeaveNotPendingError,
    LeaveOverlapsError,
    LeaveRequestNotFoundError,
    LeaveStartTooOldError,
    LeaveTypeNotFoundError,
)
from app.domains.leave.policies import ensure_can_decide
from app.domains.leave.repository import (
    LeaveRepository,
    LeaveRequestFilters,
)
from app.domains.leave.schemas import (
    LeaveBalance,
    LeaveDecision,
    LeaveRequest,
    LeaveRequestCreate,
    LeaveRequestListPage,
    LeaveRequestStatus,
    LeaveType,
    LeaveTypeCode,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

SICK_BACKDATE_DAYS = 7
PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent


class LeaveService:
    def __init__(
        self,
        repo: LeaveRepository,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._outbox = outbox

    # --- mutations -----------------------------------------------------------

    async def create_request(
        self, identity: Identity, payload: LeaveRequestCreate, now: datetime
    ) -> LeaveRequest:
        """Submit a request for the actor's employee link — always `PENDING`."""
        tenant_id = identity.tenant_id
        employee_id = identity.employee_id
        if employee_id is None:
            raise EmployeeNotFoundError()
        facts = await self._repo.employee_facts(tenant_id, employee_id)
        if facts is None:
            raise EmployeeNotFoundError()
        if facts.status != EmployeeStatus.ACTIVE:
            raise EmployeeNotActiveError()

        types = await self._repo.ensure_leave_types(tenant_id)
        leave_type = self._leave_type(types, payload.leave_type_id)
        if leave_type is None:
            raise LeaveTypeNotFoundError()

        self._ensure_start_date_allowed(leave_type.code, payload.start_date, now)
        # Serialize submits per employee while the overlap check and insert run:
        # two concurrent submits must not both pass `find_overlapping`.
        if not await self._repo.lock_employee(tenant_id, employee_id):
            raise EmployeeNotFoundError()
        if await self._repo.find_overlapping(
            tenant_id, employee_id, payload.start_date, payload.end_date
        ):
            raise LeaveOverlapsError()

        days = (payload.end_date - payload.start_date).days + 1
        if leave_type.counts_against_balance:
            balance = await self._repo.get_or_create_balance(
                tenant_id, employee_id, leave_type.id, payload.start_date.year
            )
            if balance is None:  # pragma: no cover — the type was just resolved
                raise LeaveTypeNotFoundError()
            if days > balance.remaining_days:
                raise LeaveInsufficientBalanceError()

        request = await self._repo.add_request(
            tenant_id,
            uuid7(),
            employee_id=employee_id,
            leave_type_id=leave_type.id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            days=days,
            reason=payload.reason,
        )

        await self._outbox.append(
            leave_requested(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                leave_request_id=request.id,
                employee_id=request.employee_id,
                leave_type_code=request.leave_type_code.value,
                start_date=request.start_date,
                end_date=request.end_date,
                days=request.days,
            )
        )
        changes: dict[str, Any] = {
            "employee_id": {"old": None, "new": str(request.employee_id)},
            "leave_type_id": {"old": None, "new": str(request.leave_type_id)},
            "start_date": {"old": None, "new": request.start_date.isoformat()},
            "end_date": {"old": None, "new": request.end_date.isoformat()},
            "days": {"old": None, "new": request.days},
            "status": {"old": None, "new": request.status.value},
        }
        if request.reason is not None:
            changes["reason"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="leave.requested",
                entity_type="leave_request",
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
        payload: LeaveDecision,
        now: datetime,
    ) -> LeaveRequest:
        """Approve a pending request, deducting the balance in the same transaction."""
        ensure_can_decide(identity)
        request = await self._decidable(identity, request_id)
        if request.status != LeaveRequestStatus.PENDING:
            raise LeaveNotPendingError()

        tenant_id = identity.tenant_id
        types = await self._repo.ensure_leave_types(tenant_id)
        leave_type = self._leave_type(types, request.leave_type_id)
        if leave_type is None:  # pragma: no cover — FK + seeded types guarantee it
            raise LeaveTypeNotFoundError()

        # Early, actionable refusal; the conditional UPDATE below is the
        # race-safe backstop against concurrent draws.
        if leave_type.counts_against_balance:
            balance = await self._repo.get_or_create_balance(
                tenant_id,
                request.employee_id,
                request.leave_type_id,
                request.start_date.year,
            )
            if balance is None:  # pragma: no cover — the type was just resolved
                raise LeaveTypeNotFoundError()
            if request.days > balance.remaining_days:
                raise LeaveInsufficientBalanceError()

        updated = await self._repo.transition_request(
            tenant_id,
            request_id,
            from_status=LeaveRequestStatus.PENDING,
            to_status=LeaveRequestStatus.APPROVED,
            now=now,
            decided_by=identity.actor_id,
            decided_at=now,
            decision_note=payload.decision_note,
        )
        if updated is None:
            # A concurrent decision already won — never double-apply.
            raise LeaveNotPendingError()

        if leave_type.counts_against_balance and not await self._repo.deduct_balance(
            tenant_id,
            request.employee_id,
            request.leave_type_id,
            request.start_date.year,
            request.days,
        ):
            # Raising rolls the transition back with the request transaction.
            raise LeaveInsufficientBalanceError()

        await self._outbox.append(
            leave_approved(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                leave_request_id=updated.id,
                employee_id=updated.employee_id,
                leave_type_code=updated.leave_type_code.value,
                start_date=updated.start_date,
                end_date=updated.end_date,
                days=updated.days,
                decided_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": LeaveRequestStatus.PENDING.value,
                "new": LeaveRequestStatus.APPROVED.value,
            },
            "decided_by": {"old": None, "new": str(identity.actor_id)},
        }
        if updated.decision_note is not None:
            changes["decision_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="leave.approved",
                entity_type="leave_request",
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
        payload: LeaveDecision,
        now: datetime,
    ) -> LeaveRequest:
        """Reject a pending request — the balance is never touched."""
        ensure_can_decide(identity)
        request = await self._decidable(identity, request_id)
        if request.status != LeaveRequestStatus.PENDING:
            raise LeaveNotPendingError()

        tenant_id = identity.tenant_id
        updated = await self._repo.transition_request(
            tenant_id,
            request_id,
            from_status=LeaveRequestStatus.PENDING,
            to_status=LeaveRequestStatus.REJECTED,
            now=now,
            decided_by=identity.actor_id,
            decided_at=now,
            decision_note=payload.decision_note,
        )
        if updated is None:
            raise LeaveNotPendingError()

        await self._outbox.append(
            leave_rejected(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                leave_request_id=updated.id,
                employee_id=updated.employee_id,
                leave_type_code=updated.leave_type_code.value,
                start_date=updated.start_date,
                end_date=updated.end_date,
                decided_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": LeaveRequestStatus.PENDING.value,
                "new": LeaveRequestStatus.REJECTED.value,
            },
            "decided_by": {"old": None, "new": str(identity.actor_id)},
        }
        if updated.decision_note is not None:
            changes["decision_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="leave.rejected",
                entity_type="leave_request",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def withdraw(
        self, identity: Identity, request_id: UUID, now: datetime
    ) -> LeaveRequest:
        """Withdraw the actor's own `PENDING` request — no balance to restore."""
        tenant_id = identity.tenant_id
        request = await self._repo.get_request(tenant_id, request_id)
        if request is None:
            raise LeaveRequestNotFoundError()
        if identity.employee_id is None or request.employee_id != identity.employee_id:
            # Visible standing but not the owner is a forbidden transition;
            # an out-of-scope viewer must not learn the request exists.
            if not await self._visible(identity, request.employee_id):
                raise LeaveRequestNotFoundError()
            raise ForbiddenError("Only the requester can withdraw a leave request")
        if request.status != LeaveRequestStatus.PENDING:
            raise LeaveNotPendingError()

        updated = await self._repo.transition_request(
            tenant_id,
            request_id,
            from_status=LeaveRequestStatus.PENDING,
            to_status=LeaveRequestStatus.WITHDRAWN,
            now=now,
            employee_id=identity.employee_id,
        )
        if updated is None:
            raise LeaveNotPendingError()

        await self._outbox.append(
            leave_withdrawn(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                leave_request_id=updated.id,
                employee_id=updated.employee_id,
                start_date=updated.start_date,
                end_date=updated.end_date,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="leave.withdrawn",
                entity_type="leave_request",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": LeaveRequestStatus.PENDING.value,
                        "new": LeaveRequestStatus.WITHDRAWN.value,
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
        status: LeaveRequestStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        page: PageParams,
    ) -> LeaveRequestListPage:
        """Paged requests scoped to the caller's visibility (## Roles & access)."""
        employee_ids = await self._list_scope(identity, employee_id)
        filters = LeaveRequestFilters(
            employee_ids=employee_ids,
            status=status,
            from_date=from_date,
            to_date=to_date,
        )
        items, total = await self._repo.list_requests(identity.tenant_id, filters, page)
        return LeaveRequestListPage(items=list(items), page=page.make_page(total))

    async def get_request(self, identity: Identity, request_id: UUID) -> LeaveRequest:
        """Request detail; unknown, cross-tenant and out-of-scope are all `404`."""
        request = await self._repo.get_request(identity.tenant_id, request_id)
        if request is None or not await self._visible(identity, request.employee_id):
            raise LeaveRequestNotFoundError()
        return request

    async def list_types(self, identity: Identity) -> Sequence[LeaveType]:
        """Seeded reference data, provisioned idempotently for the tenant."""
        return list(await self._repo.ensure_leave_types(identity.tenant_id))

    async def list_balances(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None = None,
        year: int | None = None,
    ) -> Sequence[LeaveBalance]:
        """Balances in the caller's visibility scope, optionally by year."""
        employee_ids = await self._list_scope(identity, employee_id)
        return await self._repo.list_balances(
            identity.tenant_id, employee_ids=employee_ids, year=year
        )

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _leave_type(
        types: Sequence[LeaveType], leave_type_id: UUID
    ) -> LeaveType | None:
        return next((row for row in types if row.id == leave_type_id), None)

    @staticmethod
    def _ensure_start_date_allowed(
        code: LeaveTypeCode, start_date: date, now: datetime
    ) -> None:
        today = now.date()
        earliest = (
            today - timedelta(days=SICK_BACKDATE_DAYS)
            if code == LeaveTypeCode.SICK
            else today
        )
        if start_date < earliest:
            raise LeaveStartTooOldError()

    async def _decidable(self, identity: Identity, request_id: UUID) -> LeaveRequest:
        """Fetch a request the caller may decide; never leaks out-of-scope ids."""
        request = await self._repo.get_request(identity.tenant_id, request_id)
        if request is None:
            raise LeaveRequestNotFoundError()
        if identity.employee_id is not None and (
            request.employee_id == identity.employee_id
        ):
            raise ForbiddenError("A leave request cannot be decided by its requester")
        if identity.role == Role.MANAGER and not await self._visible(
            identity, request.employee_id
        ):
            raise LeaveRequestNotFoundError()
        return request

    async def _visible(self, identity: Identity, employee_id: UUID) -> bool:
        """Whether the identity may see this employee's requests/balances."""
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

        An explicit `employee_id` outside the caller's visibility is a `404`
        (`employee_not_found`) — existence is not leaked.
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
        return [employee_id] if employee_id is not None else None
