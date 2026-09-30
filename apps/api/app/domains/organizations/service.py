"""Use cases for the Organizations domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/organizations.md`):

- uniqueness of `name`/`title` per tenant → `*_taken` (409); the DB unique
  constraint is the backstop for races (mapped in the Postgres repositories).
- parent must exist in the tenant → `department_not_found` (404); self or
  descendant parent → `department_cycle` (400), via a tenant-scoped ancestor
  walk. The archived state of a parent is not checked (Risk 3).
- archive checks `DepartmentUsagePort` → `department_archived_in_use` (409).
- archive-of-archived and restore-of-active are no-ops (no audit entry);
  every real transition writes an `AuditEntryCreate`.
- timezone validity via `zoneinfo` → `invalid_timezone` (422).
- time is a parameter: every mutation takes `now`, never `datetime.now()`.
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from uuid_utils.compat import uuid7

from app.domains.organizations.exceptions import (
    DepartmentArchivedInUseError,
    DepartmentCycleError,
    DepartmentNameTakenError,
    DepartmentNotFoundError,
    InvalidTimezoneError,
    LocationNameTakenError,
    LocationNotFoundError,
    PositionNotFoundError,
    PositionTitleTakenError,
)
from app.domains.organizations.repository import (
    DepartmentFilters,
    DepartmentRepository,
    DepartmentUsagePort,
    LocationFilters,
    LocationRepository,
    PositionFilters,
    PositionRepository,
)
from app.domains.organizations.schemas import (
    ArchiveRequest,
    Department,
    DepartmentCreate,
    DepartmentListPage,
    DepartmentUpdate,
    Location,
    LocationCreate,
    LocationListPage,
    LocationUpdate,
    Position,
    PositionCreate,
    PositionListPage,
    PositionUpdate,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity
from app.shared.pagination import PageParams


def _jsonable(value: Any) -> Any:
    """Make audit `changes` values JSON-safe (ids and state only — non-PII)."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise InvalidTimezoneError() from None


class DepartmentService:
    def __init__(
        self,
        repo: DepartmentRepository,
        audit: AuditWriter,
        usage: DepartmentUsagePort,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._usage = usage

    async def create(
        self, identity: Identity, payload: DepartmentCreate, now: datetime
    ) -> Department:
        tenant_id = identity.tenant_id
        if await self._repo.name_taken(tenant_id, payload.name):
            raise DepartmentNameTakenError()

        if payload.parent_department_id is not None:
            parent = await self._repo.get(tenant_id, payload.parent_department_id)
            if parent is None:
                raise DepartmentNotFoundError()
            # No cycle walk here: the id is server-generated, so a brand-new
            # node can be nobody's ancestor. Cycles are an update-time rule.

        department_id = uuid7()
        created = await self._repo.add(
            tenant_id, department_id, payload.name, payload.parent_department_id
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="department.created",
                entity_type="department",
                entity_id=created.id,
                occurred_at=now,
                changes={
                    "name": {"old": None, "new": created.name},
                    "parent_department_id": {
                        "old": None,
                        "new": _jsonable(created.parent_department_id),
                    },
                },
            )
        )
        return created

    async def get(self, identity: Identity, department_id: UUID) -> Department:
        department = await self._repo.get(identity.tenant_id, department_id)
        if department is None:
            raise DepartmentNotFoundError()
        return department

    async def list(
        self,
        identity: Identity,
        filters: DepartmentFilters,
        page: PageParams,
    ) -> DepartmentListPage:
        items, total = await self._repo.list(identity.tenant_id, filters, page)
        return DepartmentListPage(items=list(items), page=page.make_page(total))

    async def update(
        self,
        identity: Identity,
        department_id: UUID,
        payload: DepartmentUpdate,
        now: datetime,
    ) -> Department:
        tenant_id = identity.tenant_id
        existing = await self.get(identity, department_id)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        if "name" in changes and changes["name"] != existing.name:
            if await self._repo.name_taken(
                tenant_id, changes["name"], exclude_id=department_id
            ):
                raise DepartmentNameTakenError()

        if "parent_department_id" in changes:
            new_parent_id: UUID | None = changes["parent_department_id"]
            if new_parent_id is not None:
                if new_parent_id == department_id:
                    raise DepartmentCycleError()
                parent = await self._repo.get(tenant_id, new_parent_id)
                if parent is None:
                    raise DepartmentNotFoundError()
                ancestors = await self._repo.ancestor_ids(tenant_id, new_parent_id)
                if department_id in ancestors:
                    raise DepartmentCycleError()

        updated = await self._repo.update(tenant_id, department_id, **changes)
        if updated is None:  # pragma: no cover — lost race between get and update
            raise DepartmentNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="department.updated",
                entity_type="department",
                entity_id=department_id,
                occurred_at=now,
                changes={
                    field: {
                        "old": _jsonable(getattr(existing, field)),
                        "new": _jsonable(getattr(updated, field)),
                    }
                    for field in changes
                },
            )
        )
        return updated

    async def archive(
        self,
        identity: Identity,
        department_id: UUID,
        payload: ArchiveRequest | None,
        now: datetime,
    ) -> Department:
        existing = await self.get(identity, department_id)
        if existing.archived_at is not None:
            return existing  # 200 no-op, no audit entry

        in_use = await self._usage.count_active_employees(
            identity.tenant_id, department_id
        )
        if in_use > 0:
            raise DepartmentArchivedInUseError()

        updated = await self._repo.update(
            identity.tenant_id, department_id, archived_at=now
        )
        if updated is None:  # pragma: no cover — lost race
            raise DepartmentNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="department.archived",
                entity_type="department",
                entity_id=department_id,
                occurred_at=now,
                reason=payload.reason if payload else None,
            )
        )
        return updated

    async def restore(
        self, identity: Identity, department_id: UUID, now: datetime
    ) -> Department:
        existing = await self.get(identity, department_id)
        if existing.archived_at is None:
            return existing  # 200 no-op, no audit entry

        updated = await self._repo.update(
            identity.tenant_id, department_id, archived_at=None
        )
        if updated is None:  # pragma: no cover — lost race
            raise DepartmentNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="department.restored",
                entity_type="department",
                entity_id=department_id,
                occurred_at=now,
            )
        )
        return updated


class PositionService:
    def __init__(self, repo: PositionRepository, audit: AuditWriter) -> None:
        self._repo = repo
        self._audit = audit

    async def create(
        self, identity: Identity, payload: PositionCreate, now: datetime
    ) -> Position:
        tenant_id = identity.tenant_id
        if await self._repo.title_taken(tenant_id, payload.title):
            raise PositionTitleTakenError()

        position_id = uuid7()
        created = await self._repo.add(
            tenant_id, position_id, payload.title, payload.description
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="position.created",
                entity_type="position",
                entity_id=created.id,
                occurred_at=now,
                changes={"title": {"old": None, "new": created.title}},
            )
        )
        return created

    async def get(self, identity: Identity, position_id: UUID) -> Position:
        position = await self._repo.get(identity.tenant_id, position_id)
        if position is None:
            raise PositionNotFoundError()
        return position

    async def list(
        self, identity: Identity, filters: PositionFilters, page: PageParams
    ) -> PositionListPage:
        items, total = await self._repo.list(identity.tenant_id, filters, page)
        return PositionListPage(items=list(items), page=page.make_page(total))

    async def update(
        self,
        identity: Identity,
        position_id: UUID,
        payload: PositionUpdate,
        now: datetime,
    ) -> Position:
        tenant_id = identity.tenant_id
        existing = await self.get(identity, position_id)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        if "title" in changes and changes["title"] != existing.title:
            if await self._repo.title_taken(
                tenant_id, changes["title"], exclude_id=position_id
            ):
                raise PositionTitleTakenError()

        updated = await self._repo.update(tenant_id, position_id, **changes)
        if updated is None:  # pragma: no cover — lost race
            raise PositionNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="position.updated",
                entity_type="position",
                entity_id=position_id,
                occurred_at=now,
                changes={
                    field: {
                        "old": _jsonable(getattr(existing, field)),
                        "new": _jsonable(getattr(updated, field)),
                    }
                    for field in changes
                },
            )
        )
        return updated

    async def archive(
        self,
        identity: Identity,
        position_id: UUID,
        payload: ArchiveRequest | None,
        now: datetime,
    ) -> Position:
        existing = await self.get(identity, position_id)
        if existing.archived_at is not None:
            return existing  # 200 no-op, no audit entry

        updated = await self._repo.update(
            identity.tenant_id, position_id, archived_at=now
        )
        if updated is None:  # pragma: no cover — lost race
            raise PositionNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="position.archived",
                entity_type="position",
                entity_id=position_id,
                occurred_at=now,
                reason=payload.reason if payload else None,
            )
        )
        return updated

    async def restore(
        self, identity: Identity, position_id: UUID, now: datetime
    ) -> Position:
        existing = await self.get(identity, position_id)
        if existing.archived_at is None:
            return existing  # 200 no-op, no audit entry

        updated = await self._repo.update(
            identity.tenant_id, position_id, archived_at=None
        )
        if updated is None:  # pragma: no cover — lost race
            raise PositionNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="position.restored",
                entity_type="position",
                entity_id=position_id,
                occurred_at=now,
            )
        )
        return updated


class LocationService:
    def __init__(self, repo: LocationRepository, audit: AuditWriter) -> None:
        self._repo = repo
        self._audit = audit

    async def create(
        self, identity: Identity, payload: LocationCreate, now: datetime
    ) -> Location:
        tenant_id = identity.tenant_id
        _validate_timezone(payload.timezone)
        if await self._repo.name_taken(tenant_id, payload.name):
            raise LocationNameTakenError()

        location_id = uuid7()
        created = await self._repo.add(
            tenant_id, location_id, payload.name, payload.address, payload.timezone
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="location.created",
                entity_type="location",
                entity_id=created.id,
                occurred_at=now,
                changes={
                    "name": {"old": None, "new": created.name},
                    "timezone": {"old": None, "new": created.timezone},
                },
            )
        )
        return created

    async def get(self, identity: Identity, location_id: UUID) -> Location:
        location = await self._repo.get(identity.tenant_id, location_id)
        if location is None:
            raise LocationNotFoundError()
        return location

    async def list(
        self, identity: Identity, filters: LocationFilters, page: PageParams
    ) -> LocationListPage:
        items, total = await self._repo.list(identity.tenant_id, filters, page)
        return LocationListPage(items=list(items), page=page.make_page(total))

    async def update(
        self,
        identity: Identity,
        location_id: UUID,
        payload: LocationUpdate,
        now: datetime,
    ) -> Location:
        tenant_id = identity.tenant_id
        existing = await self.get(identity, location_id)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        if "timezone" in changes:
            _validate_timezone(changes["timezone"])
        if "name" in changes and changes["name"] != existing.name:
            if await self._repo.name_taken(
                tenant_id, changes["name"], exclude_id=location_id
            ):
                raise LocationNameTakenError()

        updated = await self._repo.update(tenant_id, location_id, **changes)
        if updated is None:  # pragma: no cover — lost race
            raise LocationNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="location.updated",
                entity_type="location",
                entity_id=location_id,
                occurred_at=now,
                changes={
                    field: {
                        "old": _jsonable(getattr(existing, field)),
                        "new": _jsonable(getattr(updated, field)),
                    }
                    for field in changes
                },
            )
        )
        return updated

    async def archive(
        self,
        identity: Identity,
        location_id: UUID,
        payload: ArchiveRequest | None,
        now: datetime,
    ) -> Location:
        existing = await self.get(identity, location_id)
        if existing.archived_at is not None:
            return existing  # 200 no-op, no audit entry

        updated = await self._repo.update(
            identity.tenant_id, location_id, archived_at=now
        )
        if updated is None:  # pragma: no cover — lost race
            raise LocationNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="location.archived",
                entity_type="location",
                entity_id=location_id,
                occurred_at=now,
                reason=payload.reason if payload else None,
            )
        )
        return updated

    async def restore(
        self, identity: Identity, location_id: UUID, now: datetime
    ) -> Location:
        existing = await self.get(identity, location_id)
        if existing.archived_at is None:
            return existing  # 200 no-op, no audit entry

        updated = await self._repo.update(
            identity.tenant_id, location_id, archived_at=None
        )
        if updated is None:  # pragma: no cover — lost race
            raise LocationNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="location.restored",
                entity_type="location",
                entity_id=location_id,
                occurred_at=now,
            )
        )
        return updated
