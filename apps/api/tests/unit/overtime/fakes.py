"""In-memory fakes for the Overtime domain unit tests (T4).

No I/O anywhere (`.ai/testing.md` — these tests pass with `postgres-test`
stopped). The fake honors tenant scope, the pinned filters, the open-date
overlap rule and the conditional transition guards, so the service is exercised
against the same rules as the Postgres adapter without simulating transactions.

Every transition returns `None` when its observed source state (or, for
withdraw, the ownership predicate) no longer matches — exactly the backstop the
service maps to the pinned wrong-state error. `lose_next_transition` simulates
the concurrent-decider race: the next conditional update loses even though the
service's pre-check passed.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domains.overtime.repository import (
    OPEN_STATUSES,
    EmployeeFacts,
    OvertimeRequestFilters,
)
from app.domains.overtime.schemas import (
    OvertimeRejectedFrom,
    OvertimeRequest,
    OvertimeRequestStatus,
)
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)


class FakeOvertimeRepo:
    """Tenant-scoped in-memory implementation of `OvertimeRepository`."""

    def __init__(self) -> None:
        self.employees: dict[tuple[UUID, UUID], EmployeeFacts] = {}
        self.requests: dict[tuple[UUID, UUID], OvertimeRequest] = {}
        self.calls: list[str] = []
        self.lose_next_transition = False
        self._created = 0

    def _race_lost(self) -> bool:
        """Consume a one-shot simulated lost race (the UPDATE matched 0 rows)."""
        if not self.lose_next_transition:
            return False
        self.lose_next_transition = False
        return True

    def _stamp(self) -> datetime:
        self._created += 1
        return CREATED_AT + timedelta(seconds=self._created)

    # --- employee facts ------------------------------------------------------

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        return self.employees.get((tenant_id, employee_id))

    async def lock_employee(self, tenant_id: UUID, employee_id: UUID) -> bool:
        # Unit tests are single-threaded: the lock is a presence check here,
        # recorded so tests can assert it precedes the overlap read.
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

    # --- requests ------------------------------------------------------------

    async def find_open_overlap(
        self, tenant_id: UUID, employee_id: UUID, work_date: date
    ) -> Sequence[OvertimeRequest]:
        self.calls.append("find_open_overlap")
        rows = [
            row
            for (row_tenant, _), row in self.requests.items()
            if row_tenant == tenant_id
            and row.employee_id == employee_id
            and row.work_date == work_date
            and row.status in OPEN_STATUSES
        ]
        rows.sort(key=lambda row: (row.created_at, row.id))
        return rows

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
        created_at = self._stamp()
        request = OvertimeRequest(
            id=request_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=hours,
            status=OvertimeRequestStatus.REQUESTED,
            reason=reason,
            created_at=created_at,
            updated_at=created_at,
        )
        self.requests[(tenant_id, request_id)] = request
        return request

    async def get_request(
        self, tenant_id: UUID, request_id: UUID
    ) -> OvertimeRequest | None:
        return self.requests.get((tenant_id, request_id))

    async def list_requests(
        self, tenant_id: UUID, filters: OvertimeRequestFilters, page: PageParams
    ) -> tuple[Sequence[OvertimeRequest], int]:
        if filters.employee_ids is not None and not filters.employee_ids:
            return [], 0
        allowed = None if filters.employee_ids is None else set(filters.employee_ids)
        rows = [
            row
            for (row_tenant, _), row in self.requests.items()
            if row_tenant == tenant_id
            and (allowed is None or row.employee_id in allowed)
            and (filters.status is None or row.status == filters.status)
            and (filters.from_date is None or row.work_date >= filters.from_date)
            and (filters.to_date is None or row.work_date <= filters.to_date)
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    # --- conditional transitions ---------------------------------------------

    async def transition_approve(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        approved_by: UUID,
        now: datetime,
        manager_note: str | None = None,
    ) -> OvertimeRequest | None:
        self.calls.append("transition_approve")
        if self._race_lost():
            return None
        key = (tenant_id, request_id)
        row = self.requests.get(key)
        if row is None or row.status != OvertimeRequestStatus.REQUESTED:
            return None
        updated = row.model_copy(
            update={
                "status": OvertimeRequestStatus.MANAGER_APPROVED,
                "approved_by": approved_by,
                "approved_at": now,
                "manager_note": manager_note,
                "updated_at": now,
            }
        )
        self.requests[key] = updated
        return updated

    async def transition_verify(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        verified_by: UUID,
        now: datetime,
        hr_note: str | None = None,
    ) -> OvertimeRequest | None:
        self.calls.append("transition_verify")
        if self._race_lost():
            return None
        key = (tenant_id, request_id)
        row = self.requests.get(key)
        if row is None or row.status != OvertimeRequestStatus.MANAGER_APPROVED:
            return None
        updated = row.model_copy(
            update={
                "status": OvertimeRequestStatus.HR_VERIFIED,
                "verified_by": verified_by,
                "verified_at": now,
                "hr_note": hr_note,
                "updated_at": now,
            }
        )
        self.requests[key] = updated
        return updated

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
        self.calls.append("transition_reject")
        if self._race_lost():
            return None
        key = (tenant_id, request_id)
        row = self.requests.get(key)
        if row is None or row.status.value != from_status.value:
            return None
        changes: dict[str, Any] = {
            "status": OvertimeRequestStatus.REJECTED,
            "rejected_by": rejected_by,
            "rejected_at": now,
            "rejected_from": from_status,
            "updated_at": now,
        }
        # Only the rejecting step's note is written; the other step's note (if
        # any) survives an un-verify rejection (Postgres adapter parity).
        if manager_note is not None:
            changes["manager_note"] = manager_note
        if hr_note is not None:
            changes["hr_note"] = hr_note
        updated = row.model_copy(update=changes)
        self.requests[key] = updated
        return updated

    async def transition_withdraw(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        from_status: OvertimeRequestStatus,
        employee_id: UUID,
        now: datetime,
    ) -> OvertimeRequest | None:
        self.calls.append("transition_withdraw")
        if self._race_lost():
            return None
        key = (tenant_id, request_id)
        row = self.requests.get(key)
        if row is None or row.employee_id != employee_id:
            return None
        if row.status != from_status:
            return None
        updated = row.model_copy(
            update={
                "status": OvertimeRequestStatus.WITHDRAWN,
                "withdrawn_at": now,
                "updated_at": now,
            }
        )
        self.requests[key] = updated
        return updated
