"""In-memory fakes for the Organizations domain unit tests.

No I/O anywhere (testing.md rule 4 — these tests pass with postgres-test
stopped). Fakes honor tenant scope, the pinned filters, deterministic
timestamps, and record every audit entry.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domains.organizations.repository import (
    DepartmentFilters,
    LocationFilters,
    PositionFilters,
)
from app.domains.organizations.schemas import Department, Location, Position
from app.shared.audit import AuditEntryCreate
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


class RecordingAuditWriter:
    """Append-only recorder of audit entries."""

    def __init__(self) -> None:
        self.entries: list[AuditEntryCreate] = []

    async def write(self, entry: AuditEntryCreate) -> None:
        self.entries.append(entry)


class FakeDepartmentUsage:
    """Settable usage-port fake (the seam Employees will replace)."""

    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.calls: list[tuple[UUID, UUID]] = []

    async def count_active_employees(self, tenant_id: UUID, department_id: UUID) -> int:
        self.calls.append((tenant_id, department_id))
        return self.count


class FakeDepartmentRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], Department] = {}

    async def get(self, tenant_id: UUID, department_id: UUID) -> Department | None:
        return self.rows.get((tenant_id, department_id))

    async def name_taken(
        self, tenant_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        return any(
            row.name == name and row.id != exclude_id
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        )

    async def list(
        self, tenant_id: UUID, filters: DepartmentFilters, page: PageParams
    ) -> tuple[Sequence[Department], int]:
        rows = [
            row for (row_tenant, _), row in self.rows.items() if row_tenant == tenant_id
        ]
        if not filters.include_archived:
            rows = [row for row in rows if row.archived_at is None]
        if filters.q:
            needle = filters.q.lower()
            rows = [row for row in rows if needle in row.name.lower()]
        if filters.parent_id is not None:
            rows = [
                row for row in rows if row.parent_department_id == filters.parent_id
            ]
        rows.sort(key=lambda row: row.name)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def add(
        self,
        tenant_id: UUID,
        department_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> Department:
        if await self.name_taken(tenant_id, name):  # DB unique-constraint backstop
            raise AssertionError(f"unique violation: {name!r}")
        row = Department(
            id=department_id,
            tenant_id=tenant_id,
            name=name,
            parent_department_id=parent_department_id,
            archived_at=None,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )
        self.rows[(tenant_id, department_id)] = row
        return row

    async def update(
        self, tenant_id: UUID, department_id: UUID, **changes: Any
    ) -> Department | None:
        key = (tenant_id, department_id)
        row = self.rows.get(key)
        if row is None:
            return None
        updated = row.model_copy(update={**changes, "updated_at": UPDATED_AT})
        self.rows[key] = updated
        return updated

    async def ancestor_ids(
        self, tenant_id: UUID, department_id: UUID
    ) -> Sequence[UUID]:
        ancestors: list[UUID] = []
        current = self.rows.get((tenant_id, department_id))
        seen: set[UUID] = set()
        while current is not None and current.parent_department_id is not None:
            parent_id = current.parent_department_id
            if parent_id in seen:  # pragma: no cover — fakes never store cycles
                break
            seen.add(parent_id)
            ancestors.append(parent_id)
            current = self.rows.get((tenant_id, parent_id))
        return ancestors


class FakePositionRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], Position] = {}

    async def get(self, tenant_id: UUID, position_id: UUID) -> Position | None:
        return self.rows.get((tenant_id, position_id))

    async def title_taken(
        self, tenant_id: UUID, title: str, exclude_id: UUID | None = None
    ) -> bool:
        return any(
            row.title == title and row.id != exclude_id
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        )

    async def list(
        self, tenant_id: UUID, filters: PositionFilters, page: PageParams
    ) -> tuple[Sequence[Position], int]:
        rows = [
            row for (row_tenant, _), row in self.rows.items() if row_tenant == tenant_id
        ]
        if not filters.include_archived:
            rows = [row for row in rows if row.archived_at is None]
        if filters.q:
            needle = filters.q.lower()
            rows = [row for row in rows if needle in row.title.lower()]
        rows.sort(key=lambda row: row.title)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def add(
        self, tenant_id: UUID, position_id: UUID, title: str, description: str | None
    ) -> Position:
        if await self.title_taken(tenant_id, title):
            raise AssertionError(f"unique violation: {title!r}")
        row = Position(
            id=position_id,
            tenant_id=tenant_id,
            title=title,
            description=description,
            archived_at=None,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )
        self.rows[(tenant_id, position_id)] = row
        return row

    async def update(
        self, tenant_id: UUID, position_id: UUID, **changes: Any
    ) -> Position | None:
        key = (tenant_id, position_id)
        row = self.rows.get(key)
        if row is None:
            return None
        updated = row.model_copy(update={**changes, "updated_at": UPDATED_AT})
        self.rows[key] = updated
        return updated


class FakeLocationRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], Location] = {}

    async def get(self, tenant_id: UUID, location_id: UUID) -> Location | None:
        return self.rows.get((tenant_id, location_id))

    async def name_taken(
        self, tenant_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        return any(
            row.name == name and row.id != exclude_id
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        )

    async def list(
        self, tenant_id: UUID, filters: LocationFilters, page: PageParams
    ) -> tuple[Sequence[Location], int]:
        rows = [
            row for (row_tenant, _), row in self.rows.items() if row_tenant == tenant_id
        ]
        if not filters.include_archived:
            rows = [row for row in rows if row.archived_at is None]
        if filters.q:
            needle = filters.q.lower()
            rows = [row for row in rows if needle in row.name.lower()]
        rows.sort(key=lambda row: row.name)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def add(
        self,
        tenant_id: UUID,
        location_id: UUID,
        name: str,
        address: str | None,
        timezone: str,
    ) -> Location:
        if await self.name_taken(tenant_id, name):
            raise AssertionError(f"unique violation: {name!r}")
        row = Location(
            id=location_id,
            tenant_id=tenant_id,
            name=name,
            address=address,
            timezone=timezone,
            archived_at=None,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )
        self.rows[(tenant_id, location_id)] = row
        return row

    async def update(
        self, tenant_id: UUID, location_id: UUID, **changes: Any
    ) -> Location | None:
        key = (tenant_id, location_id)
        row = self.rows.get(key)
        if row is None:
            return None
        updated = row.model_copy(update={**changes, "updated_at": UPDATED_AT})
        self.rows[key] = updated
        return updated
