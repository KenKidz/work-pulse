"""Postgres repository for the Overtime domain (Phase 4, T3).

Every statement carries `WHERE tenant_id = :tenant` first — tenant scoping
lives here and nowhere else (`.ai/security.md`). Reads/writes expose the pinned
wire shape (`schemas.py`); ORM rows never leave this module.

Transition updates are conditional on the observed source status and return
`None` when zero rows matched, so a concurrent decider's win surfaces as the
pinned wrong-state error in the service. `rejected_from` is written from that
same observed source state in one statement. Withdraw also carries the
ownership predicate, so another employee's row can never be withdrawn even if
the service guard were bypassed.

`reason`, `manager_note` and `hr_note` are stored as data only and never appear
in error messages, logs, events or audit values (`.ai/security.md`).
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as employees_orm
from app.domains.employees.schemas import EmployeeStatus
from app.domains.overtime import models as orm
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


def _request(row: orm.OvertimeRequest) -> OvertimeRequest:
    """ORM row → the pinned wire shape."""
    return OvertimeRequest.model_validate(row)


class PostgresOvertimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- employee reads ------------------------------------------------------

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        stmt = select(
            employees_orm.Employee.id,
            employees_orm.Employee.status,
            employees_orm.Employee.manager_id,
        ).where(
            employees_orm.Employee.tenant_id == tenant_id,
            employees_orm.Employee.id == employee_id,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return EmployeeFacts(
            id=row.id,
            status=EmployeeStatus(row.status),
            manager_id=row.manager_id,
        )

    async def lock_employee(self, tenant_id: UUID, employee_id: UUID) -> bool:
        """`SELECT … FOR UPDATE` on the employee row — submit's race backstop.

        Held until the request transaction commits, so a concurrent submit for
        the same employee re-checks overlaps only after this one's insert is
        visible.
        """
        stmt = (
            select(employees_orm.Employee.id)
            .where(
                employees_orm.Employee.tenant_id == tenant_id,
                employees_orm.Employee.id == employee_id,
            )
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_id: UUID | None
    ) -> Sequence[UUID]:
        if manager_id is None:
            # An unlinked manager sees nobody (attendance's pinned scope rule).
            return []
        stmt = (
            select(employees_orm.Employee.id)
            .where(
                employees_orm.Employee.tenant_id == tenant_id,
                or_(
                    employees_orm.Employee.id == manager_id,
                    employees_orm.Employee.manager_id == manager_id,
                ),
            )
            .order_by(employees_orm.Employee.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars())

    # --- requests ------------------------------------------------------------

    async def find_open_overlap(
        self, tenant_id: UUID, employee_id: UUID, work_date: date
    ) -> Sequence[OvertimeRequest]:
        stmt = (
            select(orm.OvertimeRequest)
            .where(
                orm.OvertimeRequest.tenant_id == tenant_id,
                orm.OvertimeRequest.employee_id == employee_id,
                orm.OvertimeRequest.work_date == work_date,
                orm.OvertimeRequest.status.in_(
                    tuple(status.value for status in OPEN_STATUSES)
                ),
            )
            .order_by(
                orm.OvertimeRequest.created_at.asc(),
                orm.OvertimeRequest.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_request(row) for row in rows]

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
        row = orm.OvertimeRequest(
            id=request_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=hours,
            status=OvertimeRequestStatus.REQUESTED.value,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _request(row)

    async def get_request(
        self, tenant_id: UUID, request_id: UUID
    ) -> OvertimeRequest | None:
        stmt = select(orm.OvertimeRequest).where(
            orm.OvertimeRequest.tenant_id == tenant_id,
            orm.OvertimeRequest.id == request_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _request(row) if row is not None else None

    async def list_requests(
        self, tenant_id: UUID, filters: OvertimeRequestFilters, page: PageParams
    ) -> tuple[Sequence[OvertimeRequest], int]:
        if filters.employee_ids is not None and not filters.employee_ids:
            return [], 0
        conditions = [orm.OvertimeRequest.tenant_id == tenant_id]
        if filters.employee_ids is not None:
            conditions.append(orm.OvertimeRequest.employee_id.in_(filters.employee_ids))
        if filters.status is not None:
            conditions.append(orm.OvertimeRequest.status == filters.status.value)
        if filters.from_date is not None:
            conditions.append(orm.OvertimeRequest.work_date >= filters.from_date)
        if filters.to_date is not None:
            conditions.append(orm.OvertimeRequest.work_date <= filters.to_date)

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.OvertimeRequest).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.OvertimeRequest)
            .where(*conditions)
            .order_by(
                orm.OvertimeRequest.created_at.desc(),
                orm.OvertimeRequest.id.desc(),
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).scalars()
        return [_request(row) for row in rows], total_items

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
        stmt = (
            update(orm.OvertimeRequest)
            .where(
                orm.OvertimeRequest.tenant_id == tenant_id,
                orm.OvertimeRequest.id == request_id,
                orm.OvertimeRequest.status == OvertimeRequestStatus.REQUESTED.value,
            )
            .values(
                status=OvertimeRequestStatus.MANAGER_APPROVED.value,
                approved_by=approved_by,
                approved_at=now,
                manager_note=manager_note,
                updated_at=now,
            )
            .returning(orm.OvertimeRequest.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_request(tenant_id, request_id)

    async def transition_verify(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        verified_by: UUID,
        now: datetime,
        hr_note: str | None = None,
    ) -> OvertimeRequest | None:
        stmt = (
            update(orm.OvertimeRequest)
            .where(
                orm.OvertimeRequest.tenant_id == tenant_id,
                orm.OvertimeRequest.id == request_id,
                orm.OvertimeRequest.status
                == OvertimeRequestStatus.MANAGER_APPROVED.value,
            )
            .values(
                status=OvertimeRequestStatus.HR_VERIFIED.value,
                verified_by=verified_by,
                verified_at=now,
                hr_note=hr_note,
                updated_at=now,
            )
            .returning(orm.OvertimeRequest.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_request(tenant_id, request_id)

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
        values: dict[str, Any] = {
            "status": OvertimeRequestStatus.REJECTED.value,
            "rejected_by": rejected_by,
            "rejected_at": now,
            "rejected_from": from_status.value,
            "updated_at": now,
        }
        # Only the rejecting step's note is written; the other step's note (if
        # any) must survive an un-verify rejection.
        if manager_note is not None:
            values["manager_note"] = manager_note
        if hr_note is not None:
            values["hr_note"] = hr_note
        stmt = (
            update(orm.OvertimeRequest)
            .where(
                orm.OvertimeRequest.tenant_id == tenant_id,
                orm.OvertimeRequest.id == request_id,
                orm.OvertimeRequest.status == from_status.value,
            )
            .values(**values)
            .returning(orm.OvertimeRequest.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_request(tenant_id, request_id)

    async def transition_withdraw(
        self,
        tenant_id: UUID,
        request_id: UUID,
        *,
        from_status: OvertimeRequestStatus,
        employee_id: UUID,
        now: datetime,
    ) -> OvertimeRequest | None:
        stmt = (
            update(orm.OvertimeRequest)
            .where(
                orm.OvertimeRequest.tenant_id == tenant_id,
                orm.OvertimeRequest.id == request_id,
                orm.OvertimeRequest.employee_id == employee_id,
                orm.OvertimeRequest.status == from_status.value,
            )
            .values(
                status=OvertimeRequestStatus.WITHDRAWN.value,
                withdrawn_at=now,
                updated_at=now,
            )
            .returning(orm.OvertimeRequest.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_request(tenant_id, request_id)
