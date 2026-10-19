"""In-memory fakes for the Leave domain unit tests.

No I/O anywhere (`.ai/testing.md` — these tests pass with `postgres-test`
stopped). The fake honors tenant scope, the pinned filters, inclusive overlap
semantics, conditional status transitions and the race-safe deduction guard, so
the service is exercised against the same rules as the Postgres adapter without
simulating transactions. `deduct_balance` returns `False` instead of overdrawing
and `transition_request` returns `None` when the conditional `from_status` no
longer matches — exactly the two backstops the service maps to domain errors.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domains.leave.repository import (
    SEEDED_LEAVE_TYPES,
    EmployeeFacts,
    LeaveRequestFilters,
)
from app.domains.leave.schemas import (
    LeaveBalance,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
    LeaveTypeCode,
)
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _type_id(tenant_id: UUID, code: LeaveTypeCode) -> UUID:
    """Stable per-tenant id, mirroring the seeded rows' stability."""
    return uuid5(NAMESPACE_URL, f"leave-type/{tenant_id}/{code.value}")


class FakeLeaveRepo:
    def __init__(self) -> None:
        self.types: dict[tuple[UUID, UUID], LeaveType] = {}
        self.type_defaults: dict[tuple[UUID, UUID], int] = {}
        self.employees: dict[tuple[UUID, UUID], EmployeeFacts] = {}
        self.balances: dict[tuple[UUID, UUID, UUID, int], LeaveBalance] = {}
        self.requests: dict[tuple[UUID, UUID], LeaveRequest] = {}
        self.calls: list[str] = []
        self._created = 0

    # --- reference data ------------------------------------------------------

    async def ensure_leave_types(self, tenant_id: UUID) -> Sequence[LeaveType]:
        for seeded in SEEDED_LEAVE_TYPES:
            type_id = _type_id(tenant_id, seeded.code)
            key = (tenant_id, type_id)
            if key in self.types:
                continue
            self.types[key] = LeaveType(
                id=type_id,
                code=seeded.code,
                name=seeded.name,
                counts_against_balance=seeded.counts_against_balance,
            )
            self.type_defaults[key] = seeded.default_entitlement_days
        rows = [
            row
            for (row_tenant, _), row in self.types.items()
            if row_tenant == tenant_id
        ]
        return sorted(rows, key=lambda row: row.code.value)

    # --- employee facts ------------------------------------------------------

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        return self.employees.get((tenant_id, employee_id))

    async def lock_employee(self, tenant_id: UUID, employee_id: UUID) -> bool:
        # Unit tests are single-threaded: the lock is a presence check here.
        self.calls.append("lock_employee")
        return (tenant_id, employee_id) in self.employees

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_id: UUID | None
    ) -> Sequence[UUID]:
        if manager_id is None:
            return []
        visible = [
            context.id
            for (row_tenant, _), context in self.employees.items()
            if row_tenant == tenant_id
            and (context.id == manager_id or context.manager_id == manager_id)
        ]
        return sorted(visible)

    # --- balances ------------------------------------------------------------

    async def get_or_create_balance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        year: int,
    ) -> LeaveBalance | None:
        key = (tenant_id, employee_id, leave_type_id, year)
        if key in self.balances:
            return self.balances[key]
        entitlement = self.type_defaults.get((tenant_id, leave_type_id))
        if entitlement is None:
            return None
        balance = LeaveBalance(
            tenant_id=tenant_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            year=year,
            entitlement_days=entitlement,
            used_days=0,
        )
        self.balances[key] = balance
        return balance

    async def list_balances(
        self,
        tenant_id: UUID,
        *,
        employee_ids: Sequence[UUID] | None,
        year: int | None = None,
    ) -> Sequence[LeaveBalance]:
        if employee_ids is not None and not employee_ids:
            return []
        allowed = None if employee_ids is None else set(employee_ids)
        rows = [
            row
            for (row_tenant, _, _, _), row in self.balances.items()
            if row_tenant == tenant_id
            and (allowed is None or row.employee_id in allowed)
            and (year is None or row.year == year)
        ]
        rows.sort(
            key=lambda row: (
                row.employee_id,
                self.types[(row.tenant_id, row.leave_type_id)].code.value,
                row.year,
            )
        )
        return rows

    async def deduct_balance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        year: int,
        days: int,
    ) -> bool:
        key = (tenant_id, employee_id, leave_type_id, year)
        row = self.balances.get(key)
        if row is None or row.used_days + days > row.entitlement_days:
            return False
        self.balances[key] = row.model_copy(update={"used_days": row.used_days + days})
        return True

    # --- requests ------------------------------------------------------------

    async def find_overlapping(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        start_date: date,
        end_date: date,
    ) -> Sequence[LeaveRequest]:
        self.calls.append("find_overlapping")
        blocking = {LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED}
        rows = [
            row
            for (row_tenant, _), row in self.requests.items()
            if row_tenant == tenant_id
            and row.employee_id == employee_id
            and row.status in blocking
            and row.start_date <= end_date
            and row.end_date >= start_date
        ]
        rows.sort(key=lambda row: (row.start_date, row.id))
        return rows

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
        self._created += 1
        created_at = CREATED_AT + timedelta(seconds=self._created)
        leave_type = self.types[(tenant_id, leave_type_id)]
        request = LeaveRequest(
            id=request_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            leave_type_code=leave_type.code,
            start_date=start_date,
            end_date=end_date,
            days=days,
            status=LeaveRequestStatus.PENDING,
            reason=reason,
            decision_note=None,
            decided_by=None,
            decided_at=None,
            created_at=created_at,
            updated_at=created_at,
        )
        self.requests[(tenant_id, request_id)] = request
        return request

    async def get_request(
        self, tenant_id: UUID, request_id: UUID
    ) -> LeaveRequest | None:
        return self.requests.get((tenant_id, request_id))

    async def list_requests(
        self, tenant_id: UUID, filters: LeaveRequestFilters, page: PageParams
    ) -> tuple[Sequence[LeaveRequest], int]:
        if filters.employee_ids is not None and not filters.employee_ids:
            return [], 0
        allowed = None if filters.employee_ids is None else set(filters.employee_ids)
        rows = [
            row
            for (row_tenant, _), row in self.requests.items()
            if row_tenant == tenant_id
            and (allowed is None or row.employee_id in allowed)
            and (filters.status is None or row.status == filters.status)
            and (filters.from_date is None or row.end_date >= filters.from_date)
            and (filters.to_date is None or row.start_date <= filters.to_date)
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

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
        key = (tenant_id, request_id)
        row = self.requests.get(key)
        if row is None:
            return None
        if employee_id is not None and row.employee_id != employee_id:
            return None
        if row.status != from_status:
            return None
        updated = row.model_copy(
            update={
                "status": to_status,
                "decided_by": decided_by,
                "decided_at": decided_at,
                "decision_note": decision_note,
                "updated_at": now,
            }
        )
        self.requests[key] = updated
        return updated
