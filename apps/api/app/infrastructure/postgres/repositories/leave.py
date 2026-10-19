"""Postgres repository for the Leave domain (Phase 3, T3).

Every statement carries `WHERE tenant_id = :tenant` first — tenant scoping
lives here and nowhere else (`.ai/security.md`). Reads that expose
`leave_type_code` join `leave_types`; the join also carries the tenant
condition so a cross-tenant type id can never leak a code.

Race safety:

- Seeded types and lazily-created balances use `INSERT … ON CONFLICT DO
  NOTHING` on their unique keys, so concurrent first calls converge instead of
  failing (no `IntegrityError` mapping is needed for these writes).
- Status transitions are conditional updates on the expected status; a second
  decider gets zero rows and the service maps that to `leave_not_pending`.
- The balance deduction pushes `used_days + :days <= entitlement_days` into the
  UPDATE, so concurrent approvals can never overdraw (the table check is the
  backstop) — zero rows means `leave_insufficient_balance`.

`reason` and `decision_note` are read/written as data only and never appear in
error messages, logs or events (`.ai/security.md`).
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees import models as employees_orm
from app.domains.employees.schemas import EmployeeStatus
from app.domains.leave import models as orm
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


def _request(row: orm.LeaveRequest, leave_type_code: str) -> LeaveRequest:
    """Joined ORM row → the pinned wire shape."""
    return LeaveRequest(
        id=row.id,
        tenant_id=row.tenant_id,
        employee_id=row.employee_id,
        leave_type_id=row.leave_type_id,
        leave_type_code=LeaveTypeCode(leave_type_code),
        start_date=row.start_date,
        end_date=row.end_date,
        days=row.days,
        status=LeaveRequestStatus(row.status),
        reason=row.reason,
        decision_note=row.decision_note,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresLeaveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- reference data ------------------------------------------------------

    async def ensure_leave_types(self, tenant_id: UUID) -> Sequence[LeaveType]:
        await self._session.execute(
            pg_insert(orm.LeaveType)
            .values(
                [
                    {
                        "id": uuid7(),
                        "tenant_id": tenant_id,
                        "code": seeded.code.value,
                        "name": seeded.name,
                        "counts_against_balance": seeded.counts_against_balance,
                        "default_entitlement_days": seeded.default_entitlement_days,
                    }
                    for seeded in SEEDED_LEAVE_TYPES
                ]
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "code"])
        )
        stmt = (
            select(orm.LeaveType)
            .where(orm.LeaveType.tenant_id == tenant_id)
            .order_by(orm.LeaveType.code.asc(), orm.LeaveType.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [LeaveType.model_validate(row) for row in rows]

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

    # --- balances ------------------------------------------------------------

    async def get_or_create_balance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        year: int,
    ) -> LeaveBalance | None:
        entitlement = (
            await self._session.execute(
                select(orm.LeaveType.default_entitlement_days).where(
                    orm.LeaveType.tenant_id == tenant_id,
                    orm.LeaveType.id == leave_type_id,
                )
            )
        ).scalar_one_or_none()
        if entitlement is None:
            return None

        await self._session.execute(
            pg_insert(orm.LeaveBalance)
            .values(
                id=uuid7(),
                tenant_id=tenant_id,
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                year=year,
                entitlement_days=entitlement,
                used_days=0,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "tenant_id",
                    "employee_id",
                    "leave_type_id",
                    "year",
                ]
            )
        )
        row = (
            await self._session.execute(
                select(orm.LeaveBalance).where(
                    orm.LeaveBalance.tenant_id == tenant_id,
                    orm.LeaveBalance.employee_id == employee_id,
                    orm.LeaveBalance.leave_type_id == leave_type_id,
                    orm.LeaveBalance.year == year,
                )
            )
        ).scalar_one_or_none()
        return LeaveBalance.model_validate(row) if row is not None else None

    async def list_balances(
        self,
        tenant_id: UUID,
        *,
        employee_ids: Sequence[UUID] | None,
        year: int | None = None,
    ) -> Sequence[LeaveBalance]:
        if employee_ids is not None and not employee_ids:
            return []
        conditions = [orm.LeaveBalance.tenant_id == tenant_id]
        if employee_ids is not None:
            conditions.append(orm.LeaveBalance.employee_id.in_(employee_ids))
        if year is not None:
            conditions.append(orm.LeaveBalance.year == year)
        stmt = (
            select(orm.LeaveBalance)
            .join(orm.LeaveType, orm.LeaveType.id == orm.LeaveBalance.leave_type_id)
            .where(*conditions, orm.LeaveType.tenant_id == tenant_id)
            .order_by(
                orm.LeaveBalance.employee_id.asc(),
                orm.LeaveType.code.asc(),
                orm.LeaveBalance.year.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [LeaveBalance.model_validate(row) for row in rows]

    async def deduct_balance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        year: int,
        days: int,
    ) -> bool:
        stmt = (
            update(orm.LeaveBalance)
            .where(
                orm.LeaveBalance.tenant_id == tenant_id,
                orm.LeaveBalance.employee_id == employee_id,
                orm.LeaveBalance.leave_type_id == leave_type_id,
                orm.LeaveBalance.year == year,
                orm.LeaveBalance.used_days + days <= orm.LeaveBalance.entitlement_days,
            )
            .values(used_days=orm.LeaveBalance.used_days + days)
            .returning(orm.LeaveBalance.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    # --- requests ------------------------------------------------------------

    async def find_overlapping(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        start_date: date,
        end_date: date,
    ) -> Sequence[LeaveRequest]:
        stmt = (
            select(orm.LeaveRequest, orm.LeaveType.code)
            .join(orm.LeaveType, orm.LeaveType.id == orm.LeaveRequest.leave_type_id)
            .where(
                orm.LeaveRequest.tenant_id == tenant_id,
                orm.LeaveType.tenant_id == tenant_id,
                orm.LeaveRequest.employee_id == employee_id,
                orm.LeaveRequest.status.in_(
                    (
                        LeaveRequestStatus.PENDING.value,
                        LeaveRequestStatus.APPROVED.value,
                    )
                ),
                # Inclusive overlap against the existing [start, end] range.
                orm.LeaveRequest.start_date <= end_date,
                orm.LeaveRequest.end_date >= start_date,
            )
            .order_by(
                orm.LeaveRequest.start_date.asc(),
                orm.LeaveRequest.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [_request(row, code) for row, code in rows]

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
        row = orm.LeaveRequest(
            id=request_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            start_date=start_date,
            end_date=end_date,
            days=days,
            status=LeaveRequestStatus.PENDING.value,
            reason=reason,
            decision_note=None,
            decided_by=None,
            decided_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        code = await self._leave_type_code(tenant_id, leave_type_id)
        return _request(row, code)

    async def get_request(
        self, tenant_id: UUID, request_id: UUID
    ) -> LeaveRequest | None:
        stmt = (
            select(orm.LeaveRequest, orm.LeaveType.code)
            .join(orm.LeaveType, orm.LeaveType.id == orm.LeaveRequest.leave_type_id)
            .where(
                orm.LeaveRequest.tenant_id == tenant_id,
                orm.LeaveType.tenant_id == tenant_id,
                orm.LeaveRequest.id == request_id,
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        return _request(*row) if row is not None else None

    async def list_requests(
        self, tenant_id: UUID, filters: LeaveRequestFilters, page: PageParams
    ) -> tuple[Sequence[LeaveRequest], int]:
        if filters.employee_ids is not None and not filters.employee_ids:
            return [], 0
        conditions = [orm.LeaveRequest.tenant_id == tenant_id]
        if filters.employee_ids is not None:
            conditions.append(orm.LeaveRequest.employee_id.in_(filters.employee_ids))
        if filters.status is not None:
            conditions.append(orm.LeaveRequest.status == filters.status.value)
        if filters.from_date is not None:
            conditions.append(orm.LeaveRequest.end_date >= filters.from_date)
        if filters.to_date is not None:
            conditions.append(orm.LeaveRequest.start_date <= filters.to_date)

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.LeaveRequest).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.LeaveRequest, orm.LeaveType.code)
            .join(orm.LeaveType, orm.LeaveType.id == orm.LeaveRequest.leave_type_id)
            .where(*conditions, orm.LeaveType.tenant_id == tenant_id)
            .order_by(
                orm.LeaveRequest.created_at.desc(),
                orm.LeaveRequest.id.desc(),
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).all()
        return [_request(row, code) for row, code in rows], total_items

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
        conditions = [
            orm.LeaveRequest.tenant_id == tenant_id,
            orm.LeaveRequest.id == request_id,
            orm.LeaveRequest.status == from_status.value,
        ]
        if employee_id is not None:
            conditions.append(orm.LeaveRequest.employee_id == employee_id)
        stmt = (
            update(orm.LeaveRequest)
            .where(*conditions)
            .values(
                status=to_status.value,
                decided_by=decided_by,
                decided_at=decided_at,
                decision_note=decision_note,
                updated_at=now,
            )
            .returning(orm.LeaveRequest.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_request(tenant_id, request_id)

    # --- internals -----------------------------------------------------------

    async def _leave_type_code(self, tenant_id: UUID, leave_type_id: UUID) -> str:
        code = (
            await self._session.execute(
                select(orm.LeaveType.code).where(
                    orm.LeaveType.tenant_id == tenant_id,
                    orm.LeaveType.id == leave_type_id,
                )
            )
        ).scalar_one_or_none()
        if code is None:  # pragma: no cover — the service resolved the type
            raise ValueError("leave type does not belong to the tenant")
        return code
