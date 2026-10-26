"""Postgres adapter for the Recruitment → Employees hire seam (ADR 0020).

Recruitment owns the `EmployeeHirePort` protocol (`domains/recruitment/
repository.py`); this adapter is the one place that knows both contexts. It
maps `EmployeeHirePayload` to the Employees `CreateEmployee` contract and calls
`EmployeeService.create` on the **same session** the request already owns, so
the employee row, its first employment period, `employee.created`, the
candidate's `HIRED` flip and `candidate.hired` all commit together — or not at
all (ADR 0012).

Seam errors propagate unchanged: this module never remaps
`employee_email_taken`, `employee_code_taken`, `employee_manager_invalid`,
`transfer_target_archived` or the Organizations `*_not_found` codes. A failure
therefore rolls the whole hire back and the candidate stays `OFFERED`.

The `EmployeeService` is supplied by the composition root (`app/dependencies.py`)
already built on the request session; opening a second session here would
silently break the atomicity guarantee.
"""

from datetime import datetime
from uuid import UUID

from app.domains.employees.schemas import CreateEmployee, EmploymentType
from app.domains.employees.service import EmployeeService
from app.domains.recruitment.repository import EmployeeHirePayload
from app.shared.identity import Identity, Role


class PostgresEmployeeHirePort:
    """Recruitment's in-process hire seam over the Employees service."""

    def __init__(self, employees: EmployeeService) -> None:
        self._employees = employees

    async def create_employee(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        payload: EmployeeHirePayload,
        now: datetime,
    ) -> UUID:
        """Create the Employee through `EmployeeService.create`; return its id."""
        # `EmployeeService.create` does not read `role`; the identity carries
        # the real tenant and acting user so `employee.created`/audit keep the
        # correct actor (ADR 0010, ADR 0020 decision 2).
        identity = Identity(
            tenant_id=tenant_id,
            actor_id=actor_id,
            role=Role.HR,
            employee_id=None,
        )
        employee = await self._employees.create(
            identity,
            CreateEmployee(
                first_name=payload.first_name,
                last_name=payload.last_name,
                email=payload.email,
                phone=payload.phone,
                department_id=payload.department_id,
                position_id=payload.position_id,
                location_id=payload.location_id,
                manager_id=payload.manager_id,
                employment_type=EmploymentType(payload.employment_type),
                hired_at=payload.hired_at,
                employee_code=payload.employee_code,
            ),
            now,
        )
        return employee.id
