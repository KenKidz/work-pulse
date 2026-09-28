"""Real `DepartmentUsagePort` implementation (organizations Risk 1, ADR 0007).

Counts employees with an OPEN employment period in the department whose status
is `active` or `on_leave`. Infrastructure implements the port defined by the
Organizations domain — no domain imports another domain's internals
(`NullDepartmentUsage` remains the unit-test fake only).
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as employee_orm
from app.domains.employees.schemas import EmployeeStatus


class PostgresEmployeeUsage:
    """`domains/organizations/repository.py::DepartmentUsagePort` over Postgres."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_active_employees(self, tenant_id: UUID, department_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(employee_orm.Employment)
            .join(
                employee_orm.Employee,
                employee_orm.Employment.employee_id == employee_orm.Employee.id,
            )
            .where(
                employee_orm.Employment.tenant_id == tenant_id,
                employee_orm.Employment.department_id == department_id,
                employee_orm.Employment.effective_to.is_(None),
                employee_orm.Employee.tenant_id == tenant_id,
                employee_orm.Employee.status.in_(
                    [EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_LEAVE.value]
                ),
            )
        )
        return (await self._session.execute(stmt)).scalar_one()
