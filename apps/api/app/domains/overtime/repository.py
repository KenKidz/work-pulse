"""Data-access abstractions for the Overtime domain (Phase 4, T3).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the pinned wire schemas (`schemas.py`); ORM translation
happens inside the Postgres adapter.

`EmployeeFacts` is the minimal employee read overtime rules need: status for
`employee_not_active` and `manager_id` for the manager's team scope. The
adapter reads the `employees` table directly — the same cross-domain read the
leave and attendance adapters perform — and overtime never imports employee
internals.

`OvertimeRequestFilters` carries the *effective* filters: the service resolves
the current user's visibility into `employee_ids` before calling
`list_requests`. `from_date`/`to_date` filter `work_date` **inclusively** (T1).
`employee_ids is None` means "no employee scoping" (HR/Admin/Super Admin); an
empty sequence matches nothing.

Transitions are conditional updates on the observed source state. A `None`
return means the state guard lost the race (another decider already moved the
row) or the row is not in the tenant; the service maps both to the pinned
wrong-state error. `rejected_from` is written from the observed source state,
so a reject can never claim a state it did not see.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.employees.schemas import EmployeeStatus
from app.domains.overtime.schemas import (
    OvertimeRejectedFrom,
    OvertimeRequest,
    OvertimeRequestStatus,
)
from app.shared.pagination import PageParams

# States that block a new claim on the same `work_date`. `included_in_payroll`
# is deliberately absent: counted rows no longer block an additional-hours
# claim (the payroll term counts each row individually).
OPEN_STATUSES: tuple[OvertimeRequestStatus, ...] = (
    OvertimeRequestStatus.REQUESTED,
    OvertimeRequestStatus.MANAGER_APPROVED,
    OvertimeRequestStatus.HR_VERIFIED,
)


@dataclass(frozen=True, slots=True)
class EmployeeFacts:
    """Minimal employee facts overtime rules need (id, status, manager)."""

    id: UUID
    status: EmployeeStatus
    manager_id: UUID | None


class OvertimeRequestFilters(BaseModel):
    """Effective request-list filters (visibility already resolved)."""

    employee_ids: Sequence[UUID] | None = None
    status: OvertimeRequestStatus | None = None
    from_date: date | None = None
    to_date: date | None = None


class OvertimeRepository(Protocol):
    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        """Tenant-scoped employee facts; `None` when the employee is unknown."""
        ...

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_id: UUID | None
    ) -> Sequence[UUID]:
        """A manager's self + direct reports; empty for an unlinked manager."""
        ...

    async def find_open_overlap(
        self, tenant_id: UUID, employee_id: UUID, work_date: date
    ) -> Sequence[OvertimeRequest]:
        """Open (`requested`/`manager_approved`/`hr_verified`) claims on a date.

        Rejected/withdrawn rows never block, and `included_in_payroll` rows do
        not either — a later claim pays its own verified hours.
        """
        ...

    async def create_request(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        employee_id: UUID,
        work_date: date,
        hours: Decimal,
        reason: str | None,
    ) -> OvertimeRequest:
        """Insert the claim in `requested`; returns the wire shape."""
        ...

    async def get_request(
        self, tenant_id: UUID, request_id: UUID
    ) -> OvertimeRequest | None:
        """Tenant-scoped detail; `None` for a missing or foreign id."""
        ...

    async def list_requests(
        self, tenant_id: UUID, filters: OvertimeRequestFilters, page: PageParams
    ) -> tuple[Sequence[OvertimeRequest], int]:
        """Paged requests, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def transition_approve(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        approved_by: UUID,
        now: datetime,
        manager_note: str | None = None,
    ) -> OvertimeRequest | None:
        """`requested → manager_approved` (`WHERE status = 'requested'`)."""
        ...

    async def transition_verify(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        verified_by: UUID,
        now: datetime,
        hr_note: str | None = None,
    ) -> OvertimeRequest | None:
        """`manager_approved → hr_verified` (`WHERE status =
        'manager_approved'`)."""
        ...

    async def transition_reject(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        from_status: OvertimeRejectedFrom,
        rejected_by: UUID,
        now: datetime,
        manager_note: str | None = None,
        hr_note: str | None = None,
    ) -> OvertimeRequest | None:
        """Reject from the observed open state and record `rejected_from`.

        The note column is supplied by the service's step routing (manager step
        from `requested`, HR step from `manager_approved`/`hr_verified`); an
        unsupplied note never clears the other step's stored note. `None` when
        the row is no longer in `from_status`.
        """
        ...

    async def transition_withdraw(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        employee_id: UUID,
        now: datetime,
    ) -> OvertimeRequest | None:
        """Own `requested|manager_approved → withdrawn`.

        The ownership predicate is part of the update, so the repository can
        never withdraw another employee's row. `None` when the row is not in an
        open pre-verification state or not owned by `employee_id`.
        """
        ...
