"""Data-access abstractions for the Employees domain.

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the wire schemas (`schemas.py`); ORM translation happens
inside the Postgres adapters.

`update(**changes)` semantics: only the keys present are written. Allowed keys:
`first_name`, `last_name`, `email`, `phone`, `manager_id`, `department_id`,
`position_id`, `location_id`, `status`, `terminated_at`, `employment_type`.

The organization reference checks on create/transfer go through the
organizations services (conventions: go through the other domain's service);
this module stays free of cross-domain ports.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.employees.schemas import (
    Employee,
    EmployeeStatus,
    Employment,
    EmploymentType,
)
from app.shared.pagination import PageParams


class EmployeeFilters(BaseModel):
    """List filters (pinned contract: q + one filter per reference/state)."""

    q: str | None = None
    department_id: UUID | None = None
    position_id: UUID | None = None
    location_id: UUID | None = None
    status: EmployeeStatus | None = None
    manager_id: UUID | None = None


class EmployeeRepository(Protocol):
    async def get(self, tenant_id: UUID, employee_id: UUID) -> Employee | None: ...

    async def email_taken(
        self, tenant_id: UUID, email: str, exclude_id: UUID | None = None
    ) -> bool: ...

    async def code_taken(self, tenant_id: UUID, employee_code: str) -> bool: ...

    async def next_employee_code(self, tenant_id: UUID) -> str:
        """Next auto code for the tenant: `EMP-` + (max numeric suffix + 1).

        Non-numeric overrides (`EMP-TEMP`) are ignored by the scan; concurrent
        creates may pick the same code and are mapped to `employee_code_taken`
        by the unique constraint (accepted Phase-1 race, spec Risk 3).
        """
        ...

    async def list(
        self, tenant_id: UUID, filters: EmployeeFilters, page: PageParams
    ) -> tuple[Sequence[Employee], int]: ...

    async def add(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        employee_code: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
        manager_id: UUID | None,
        employment_type: EmploymentType,
        status: EmployeeStatus,
        hired_at: date,
    ) -> Employee: ...

    async def update(
        self, tenant_id: UUID, employee_id: UUID, **changes: Any
    ) -> Employee | None: ...

    async def direct_reports(
        self, tenant_id: UUID, manager_id: UUID
    ) -> Sequence[Employee]: ...

    async def clear_manager(self, tenant_id: UUID, manager_id: UUID) -> Sequence[UUID]:
        """Null `manager_id` on every direct report; returns the report ids."""
        ...


class EmploymentRepository(Protocol):
    """The append-only employment history. `close` is the only mutation."""

    async def current(self, tenant_id: UUID, employee_id: UUID) -> Employment | None:
        """The open period (`effective_to IS NULL`), if any."""
        ...

    async def history(self, tenant_id: UUID, employee_id: UUID) -> Sequence[Employment]:
        """All periods, newest first."""
        ...

    async def add(
        self,
        tenant_id: UUID,
        employment_id: UUID,
        *,
        employee_id: UUID,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
        employment_type: EmploymentType,
        effective_from: date,
        effective_to: date | None = None,
    ) -> Employment: ...

    async def close(
        self, tenant_id: UUID, employment_id: UUID, effective_to: date
    ) -> Employment | None: ...
