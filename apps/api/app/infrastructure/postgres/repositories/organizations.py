"""Postgres repositories for the Organizations domain.

Every statement carries `WHERE tenant_id = :tenant` — tenant scoping lives
here and nowhere else (`.ai/security.md`). Unique-violation `IntegrityError`s
are mapped to the domain `*_taken` errors (the DB constraint is the race
backstop behind the service pre-check).
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.organizations import models as orm
from app.domains.organizations.exceptions import (
    DepartmentNameTakenError,
    LocationNameTakenError,
    PositionTitleTakenError,
)
from app.domains.organizations.repository import (
    DepartmentFilters,
    LocationFilters,
    PositionFilters,
)
from app.domains.organizations.schemas import Department, Location, Position
from app.infrastructure.postgres.models_audit import (
    AuditEntry,  # noqa: F401  (metadata)
)
from app.shared.pagination import PageParams

_LIKE_ESCAPE = "\\"


def _contains(term: str) -> str:
    """Build an ILIKE pattern for substring search, escaping user wildcards."""
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


class PostgresDepartmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID, department_id: UUID) -> Department | None:
        stmt = select(orm.Department).where(
            orm.Department.id == department_id,
            orm.Department.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Department.model_validate(row) if row is not None else None

    async def name_taken(
        self, tenant_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        stmt = select(orm.Department.id).where(
            orm.Department.tenant_id == tenant_id,
            orm.Department.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.Department.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def list(
        self, tenant_id: UUID, filters: DepartmentFilters, page: PageParams
    ) -> tuple[Sequence[Department], int]:
        conditions = [orm.Department.tenant_id == tenant_id]
        if not filters.include_archived:
            conditions.append(orm.Department.archived_at.is_(None))
        if filters.q:
            conditions.append(
                orm.Department.name.ilike(_contains(filters.q), escape=_LIKE_ESCAPE)
            )
        if filters.parent_id is not None:
            conditions.append(orm.Department.parent_department_id == filters.parent_id)

        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.Department).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(orm.Department)
                .where(*conditions)
                .order_by(orm.Department.name.asc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return [Department.model_validate(row) for row in rows], total

    async def add(
        self,
        tenant_id: UUID,
        department_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> Department:
        row = orm.Department(
            id=department_id,
            tenant_id=tenant_id,
            name=name,
            parent_department_id=parent_department_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Race backstop for (tenant_id, name) — the service pre-checks.
            raise DepartmentNameTakenError() from exc
        await self._session.refresh(row)
        return Department.model_validate(row)

    async def update(
        self, tenant_id: UUID, department_id: UUID, **changes: Any
    ) -> Department | None:
        stmt = select(orm.Department).where(
            orm.Department.id == department_id,
            orm.Department.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return Department.model_validate(row)

    async def ancestor_ids(
        self, tenant_id: UUID, department_id: UUID
    ) -> Sequence[UUID]:
        """Tenant-scoped ancestor walk (recursive CTE), nearest first."""
        base = select(
            orm.Department.id.label("id"),
            orm.Department.parent_department_id.label("pid"),
        ).where(
            orm.Department.id == department_id,
            orm.Department.tenant_id == tenant_id,
        )
        cte = base.cte("department_ancestors", recursive=True)
        step = select(orm.Department.id, orm.Department.parent_department_id).where(
            orm.Department.id == cte.c.pid,
            orm.Department.tenant_id == tenant_id,
        )
        cte = cte.union_all(step)
        stmt = select(cte.c.pid).where(cte.c.pid.is_not(None))
        return [row[0] for row in (await self._session.execute(stmt)).all()]


class PostgresPositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID, position_id: UUID) -> Position | None:
        stmt = select(orm.Position).where(
            orm.Position.id == position_id,
            orm.Position.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Position.model_validate(row) if row is not None else None

    async def title_taken(
        self, tenant_id: UUID, title: str, exclude_id: UUID | None = None
    ) -> bool:
        stmt = select(orm.Position.id).where(
            orm.Position.tenant_id == tenant_id,
            orm.Position.title == title,
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.Position.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def list(
        self, tenant_id: UUID, filters: PositionFilters, page: PageParams
    ) -> tuple[Sequence[Position], int]:
        conditions = [orm.Position.tenant_id == tenant_id]
        if not filters.include_archived:
            conditions.append(orm.Position.archived_at.is_(None))
        if filters.q:
            conditions.append(
                orm.Position.title.ilike(_contains(filters.q), escape=_LIKE_ESCAPE)
            )

        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.Position).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(orm.Position)
                .where(*conditions)
                .order_by(orm.Position.title.asc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return [Position.model_validate(row) for row in rows], total

    async def add(
        self, tenant_id: UUID, position_id: UUID, title: str, description: str | None
    ) -> Position:
        row = orm.Position(
            id=position_id,
            tenant_id=tenant_id,
            title=title,
            description=description,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise PositionTitleTakenError() from exc
        await self._session.refresh(row)
        return Position.model_validate(row)

    async def update(
        self, tenant_id: UUID, position_id: UUID, **changes: Any
    ) -> Position | None:
        stmt = select(orm.Position).where(
            orm.Position.id == position_id,
            orm.Position.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return Position.model_validate(row)


class PostgresLocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID, location_id: UUID) -> Location | None:
        stmt = select(orm.Location).where(
            orm.Location.id == location_id,
            orm.Location.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Location.model_validate(row) if row is not None else None

    async def name_taken(
        self, tenant_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        stmt = select(orm.Location.id).where(
            orm.Location.tenant_id == tenant_id,
            orm.Location.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.Location.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def list(
        self, tenant_id: UUID, filters: LocationFilters, page: PageParams
    ) -> tuple[Sequence[Location], int]:
        conditions = [orm.Location.tenant_id == tenant_id]
        if not filters.include_archived:
            conditions.append(orm.Location.archived_at.is_(None))
        if filters.q:
            conditions.append(
                orm.Location.name.ilike(_contains(filters.q), escape=_LIKE_ESCAPE)
            )

        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.Location).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(orm.Location)
                .where(*conditions)
                .order_by(orm.Location.name.asc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return [Location.model_validate(row) for row in rows], total

    async def add(
        self,
        tenant_id: UUID,
        location_id: UUID,
        name: str,
        address: str | None,
        timezone: str,
    ) -> Location:
        row = orm.Location(
            id=location_id,
            tenant_id=tenant_id,
            name=name,
            address=address,
            timezone=timezone,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise LocationNameTakenError() from exc
        await self._session.refresh(row)
        return Location.model_validate(row)

    async def update(
        self, tenant_id: UUID, location_id: UUID, **changes: Any
    ) -> Location | None:
        stmt = select(orm.Location).where(
            orm.Location.id == location_id,
            orm.Location.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return Location.model_validate(row)
