"""Data-access abstractions for the Organizations domain.

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.

Repositories speak the wire schemas (`schemas.py`); ORM translation happens
inside the Postgres adapters.

`update(**changes)` semantics: only the keys present are written. Allowed
keys per collection:

- departments: `name`, `parent_department_id` (None moves to the root),
  `archived_at` (None restores).
- positions: `title`, `description`, `archived_at`.
- locations: `name`, `address`, `timezone`, `archived_at`.
"""

from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.organizations.schemas import Department, Location, Position
from app.shared.pagination import PageParams


class DepartmentFilters(BaseModel):
    """List filters for departments (pinned contract)."""

    q: str | None = None
    parent_id: UUID | None = None
    include_archived: bool = False


class PositionFilters(BaseModel):
    q: str | None = None
    include_archived: bool = False


class LocationFilters(BaseModel):
    q: str | None = None
    include_archived: bool = False


class DepartmentRepository(Protocol):
    async def get(self, tenant_id: UUID, department_id: UUID) -> Department | None: ...

    async def name_taken(
        self, tenant_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool: ...

    async def list(
        self, tenant_id: UUID, filters: DepartmentFilters, page: PageParams
    ) -> tuple[Sequence[Department], int]: ...

    async def add(
        self,
        tenant_id: UUID,
        department_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> Department: ...

    async def update(
        self, tenant_id: UUID, department_id: UUID, **changes: Any
    ) -> Department | None: ...

    async def ancestor_ids(
        self, tenant_id: UUID, department_id: UUID
    ) -> Sequence[UUID]:
        """Ids of all ancestors above the given department (tenant-scoped walk)."""
        ...


class PositionRepository(Protocol):
    async def get(self, tenant_id: UUID, position_id: UUID) -> Position | None: ...

    async def title_taken(
        self, tenant_id: UUID, title: str, exclude_id: UUID | None = None
    ) -> bool: ...

    async def list(
        self, tenant_id: UUID, filters: PositionFilters, page: PageParams
    ) -> tuple[Sequence[Position], int]: ...

    async def add(
        self,
        tenant_id: UUID,
        position_id: UUID,
        title: str,
        description: str | None,
    ) -> Position: ...

    async def update(
        self, tenant_id: UUID, position_id: UUID, **changes: Any
    ) -> Position | None: ...


class LocationRepository(Protocol):
    async def get(self, tenant_id: UUID, location_id: UUID) -> Location | None: ...

    async def name_taken(
        self, tenant_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool: ...

    async def list(
        self, tenant_id: UUID, filters: LocationFilters, page: PageParams
    ) -> tuple[Sequence[Location], int]: ...

    async def add(
        self,
        tenant_id: UUID,
        location_id: UUID,
        name: str,
        address: str | None,
        timezone: str,
    ) -> Location: ...

    async def update(
        self, tenant_id: UUID, location_id: UUID, **changes: Any
    ) -> Location | None: ...


class DepartmentUsagePort(Protocol):
    """Seam the Employees feature replaces with a real count.

    Archiving a department that still has active employees is blocked
    (PO Decision #1 → `409 department_archived_in_use`).
    """

    async def count_active_employees(
        self, tenant_id: UUID, department_id: UUID
    ) -> int: ...


class NullDepartmentUsage:
    """Phase 1 default: no employees table exists yet, so nothing is in use."""

    async def count_active_employees(self, tenant_id: UUID, department_id: UUID) -> int:
        return 0
