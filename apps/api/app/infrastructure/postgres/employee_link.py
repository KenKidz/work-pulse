"""Real `EmployeeLinkPort` implementation (identity → employees, ADR 0007 precedent).

Existence check over `employees`, tenant-scoped. Infrastructure implements the
port defined by the Identity domain — no domain imports another domain's
tables.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as employee_orm


class PostgresEmployeeLink:
    """`domains/identity/repository.py::EmployeeLinkPort` over Postgres."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists(self, tenant_id: UUID, employee_id: UUID) -> bool:
        stmt = select(employee_orm.Employee.id).where(
            employee_orm.Employee.id == employee_id,
            employee_orm.Employee.tenant_id == tenant_id,
        )
        return (await self._session.execute(stmt.limit(1))).first() is not None
