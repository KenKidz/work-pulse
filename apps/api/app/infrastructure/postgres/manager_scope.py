"""Read-only manager scope for the reports read side (T5, ADR 0019).

The exact same self + direct-reports query that gates the leave/overtime
inboxes (`visible_employee_ids`), copied behind the reports-owned
`EmployeeScope` port. Report scope and inbox scope must never disagree, so the
predicate is intentionally identical: `employee.id = manager OR
employee.manager_id = manager`, one level, tenant-scoped first. An unlinked
manager sees nobody.

Read-only: this adapter never mutates the Employees context.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as employee_orm


class PostgresEmployeeScope:
    """`EmployeeScope` over the Employees table (read-only, per tenant)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> Sequence[UUID]:
        if manager_employee_id is None:
            return ()
        stmt = (
            select(employee_orm.Employee.id)
            .where(
                employee_orm.Employee.tenant_id == tenant_id,
                or_(
                    employee_orm.Employee.id == manager_employee_id,
                    employee_orm.Employee.manager_id == manager_employee_id,
                ),
            )
            .order_by(employee_orm.Employee.id.asc())
        )
        return tuple((await self._session.execute(stmt)).scalars())


__all__ = ["PostgresEmployeeScope"]
