"""Data-access abstractions for the Leave domain (Phase 3, T3).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the pinned wire schemas (`schemas.py`); ORM translation
happens inside the Postgres adapter.

Two intentional exceptions carry domain-internal facts:

- `EmployeeFacts` is the minimal employee read leave rules need (status for
  `employee_not_active`/termination and `manager_id` for team scope). The
  adapter reads the `employees` table directly — the same cross-domain read the
  attendance adapter performs — and leave never imports employee internals.
- `SEEDED_LEAVE_TYPES` is the v1 reference data provisioned per tenant by
  `ensure_leave_types`. Values are fixture data, not product policy: annual 12,
  sick 6, unpaid 0 (confirmed at T3; `.ai/features/leave.md` risk 2).

`LeaveRequestFilters` carries the *effective* filters: the service resolves the
current user's visibility into `employee_ids` before calling `list_requests`.
`from_date`/`to_date` match requests whose inclusive range **overlaps** the
window (a request spanning the window edge is included).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.employees.schemas import EmployeeStatus
from app.domains.leave.schemas import (
    LeaveBalance,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
    LeaveTypeCode,
)
from app.shared.pagination import PageParams


@dataclass(frozen=True, slots=True)
class SeededLeaveType:
    """One provisioned leave type and its v1 default entitlement."""

    code: LeaveTypeCode
    name: str
    counts_against_balance: bool
    default_entitlement_days: int


SEEDED_LEAVE_TYPES: tuple[SeededLeaveType, ...] = (
    SeededLeaveType(
        code=LeaveTypeCode.ANNUAL,
        name="Annual leave",
        counts_against_balance=True,
        default_entitlement_days=12,
    ),
    SeededLeaveType(
        code=LeaveTypeCode.SICK,
        name="Sick leave",
        counts_against_balance=True,
        default_entitlement_days=6,
    ),
    SeededLeaveType(
        code=LeaveTypeCode.UNPAID,
        name="Unpaid leave",
        counts_against_balance=False,
        default_entitlement_days=0,
    ),
)


@dataclass(frozen=True, slots=True)
class EmployeeFacts:
    """Minimal employee facts leave rules need (id, status, manager)."""

    id: UUID
    status: EmployeeStatus
    manager_id: UUID | None


class LeaveRequestFilters(BaseModel):
    """Effective request-list filters (visibility already resolved).

    `employee_ids is None` means "no employee scoping" (hr/admin/super_admin);
    an empty sequence matches nothing. `from_date`/`to_date` are inclusive
    overlap bounds.
    """

    employee_ids: Sequence[UUID] | None = None
    status: LeaveRequestStatus | None = None
    from_date: date | None = None
    to_date: date | None = None


class LeaveRepository(Protocol):
    async def ensure_leave_types(self, tenant_id: UUID) -> Sequence[LeaveType]:
        """Idempotently provision the three seeded types; return them all.

        Safe under concurrent first calls (unique `(tenant_id, code)` with an
        `ON CONFLICT DO NOTHING` insert). Ordered by code for a stable wire
        response.
        """
        ...

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        """Tenant-scoped employee facts; `None` when the employee is unknown."""
        ...

    async def lock_employee(self, tenant_id: UUID, employee_id: UUID) -> bool:
        """Take the employee's row lock for the rest of the transaction.

        Submit serializes per employee: the overlap check and the insert run
        while this lock is held, so two concurrent submits cannot both pass
        `find_overlapping` and insert overlapping ranges (the check-then-insert
        race has no declarative constraint — the row lock is the backstop).
        `False` when the employee row is not in the tenant.
        """
        ...

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_id: UUID | None
    ) -> Sequence[UUID]:
        """A manager's self + direct reports; empty for an unlinked manager."""
        ...

    async def get_or_create_balance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        year: int,
    ) -> LeaveBalance | None:
        """Lazily create the balance from the type's default entitlement.

        `None` when the leave type does not exist in the tenant (the service
        resolved it earlier, so this is a defensive read). Race-safe under
        concurrent creation via the `(tenant, employee, type, year)` unique key.
        """
        ...

    async def find_overlapping(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        start_date: date,
        end_date: date,
    ) -> Sequence[LeaveRequest]:
        """`PENDING`/`APPROVED` requests intersecting the range inclusively.

        `REJECTED`/`WITHDRAWN` never block. Ordered by start date then id.
        """
        ...

    async def add_request(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        employee_id: UUID,
        leave_type_id: UUID,
        start_date: date,
        end_date: date,
        days: int,
        reason: str | None,
    ) -> LeaveRequest:
        """Insert the request in `PENDING`; returns the wire shape with code."""
        ...

    async def get_request(
        self, tenant_id: UUID, request_id: UUID
    ) -> LeaveRequest | None:
        """Tenant-scoped detail with the joined `leave_type_code`."""
        ...

    async def list_requests(
        self, tenant_id: UUID, filters: LeaveRequestFilters, page: PageParams
    ) -> tuple[Sequence[LeaveRequest], int]:
        """Paged requests, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def list_balances(
        self,
        tenant_id: UUID,
        *,
        employee_ids: Sequence[UUID] | None,
        year: int | None = None,
    ) -> Sequence[LeaveBalance]:
        """Balances for the effective scope/year, ordered employee then code."""
        ...

    async def transition_request(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        from_status: LeaveRequestStatus,
        to_status: LeaveRequestStatus,
        now: datetime,
        decided_by: UUID | None = None,
        decided_at: datetime | None = None,
        decision_note: str | None = None,
        employee_id: UUID | None = None,
    ) -> LeaveRequest | None:
        """Conditional status transition (`WHERE status = from_status`).

        `None` when the request is not in `from_status` (a concurrent decision
        already won) or does not exist in the tenant. `employee_id` adds the
        ownership guard used by withdraw. `decision_note` is PII and stays in
        the database only.
        """
        ...

    async def deduct_balance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        year: int,
        days: int,
    ) -> bool:
        """Race-safe `used_days + days <= entitlement_days` deduction.

        `False` when the row is missing or the deduction would overdraw —
        the caller maps that to `leave_insufficient_balance`. The update and
        the request transition share the caller's transaction.
        """
        ...
