"""Repository-level integration tests: tenant scoping, filters, pagination, races."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

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
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.shared.pagination import PageParams
from tests.integration.conftest import TENANT_A, TENANT_B

ARCHIVED_AT = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def departments(session: AsyncSession) -> PostgresDepartmentRepository:
    return PostgresDepartmentRepository(session)


@pytest.fixture
def positions(session: AsyncSession) -> PostgresPositionRepository:
    return PostgresPositionRepository(session)


@pytest.fixture
def locations(session: AsyncSession) -> PostgresLocationRepository:
    return PostgresLocationRepository(session)


async def test_department_tenant_scoping_both_directions(
    departments: PostgresDepartmentRepository,
) -> None:
    id_a = uuid7()
    id_b = uuid7()
    await departments.add(TENANT_A, id_a, "Engineering", None)
    await departments.add(TENANT_B, id_b, "Engineering", None)

    assert (await departments.get(TENANT_A, id_a)) is not None
    assert await departments.get(TENANT_B, id_a) is None  # A's row invisible to B
    assert await departments.get(TENANT_A, id_b) is None  # B's row invisible to A

    items_a, total_a = await departments.list(
        TENANT_A, DepartmentFilters(), PageParams()
    )
    items_b, total_b = await departments.list(
        TENANT_B, DepartmentFilters(), PageParams()
    )
    assert total_a == 1 and [item.id for item in items_a] == [id_a]
    assert total_b == 1 and [item.id for item in items_b] == [id_b]


async def test_department_name_taken_is_tenant_scoped(
    departments: PostgresDepartmentRepository,
) -> None:
    await departments.add(TENANT_A, uuid7(), "Engineering", None)

    assert await departments.name_taken(TENANT_A, "Engineering") is True
    assert await departments.name_taken(TENANT_B, "Engineering") is False


async def test_department_q_filter_is_case_insensitive(
    departments: PostgresDepartmentRepository,
) -> None:
    await departments.add(TENANT_A, uuid7(), "Engineering", None)
    await departments.add(TENANT_A, uuid7(), "People Ops", None)

    items, total = await departments.list(
        TENANT_A, DepartmentFilters(q="engine"), PageParams()
    )

    assert total == 1
    assert [item.name for item in items] == ["Engineering"]


async def test_department_parent_filter(
    departments: PostgresDepartmentRepository,
) -> None:
    root_id = uuid7()
    await departments.add(TENANT_A, root_id, "Company", None)
    await departments.add(TENANT_A, uuid7(), "Backend", root_id)
    await departments.add(TENANT_A, uuid7(), "Frontend", root_id)
    await departments.add(TENANT_A, uuid7(), "Standalone", None)

    items, total = await departments.list(
        TENANT_A, DepartmentFilters(parent_id=root_id), PageParams()
    )

    assert total == 2
    assert [item.name for item in items] == ["Backend", "Frontend"]


async def test_department_include_archived(
    departments: PostgresDepartmentRepository,
) -> None:
    active_id = uuid7()
    archived_id = uuid7()
    await departments.add(TENANT_A, active_id, "Active", None)
    await departments.add(TENANT_A, archived_id, "Archived", None)
    await departments.update(TENANT_A, archived_id, archived_at=ARCHIVED_AT)

    _, total_default = await departments.list(
        TENANT_A, DepartmentFilters(), PageParams()
    )
    items_all, total_all = await departments.list(
        TENANT_A, DepartmentFilters(include_archived=True), PageParams()
    )

    assert total_default == 1
    assert total_all == 2
    assert {item.name for item in items_all} == {"Active", "Archived"}


async def test_department_pagination_totals(
    departments: PostgresDepartmentRepository,
) -> None:
    for index in range(25):
        await departments.add(TENANT_A, uuid7(), f"dept-{index:02d}", None)

    page1, total = await departments.list(
        TENANT_A, DepartmentFilters(), PageParams(page=1, page_size=20)
    )
    page2, _ = await departments.list(
        TENANT_A, DepartmentFilters(), PageParams(page=2, page_size=20)
    )

    assert total == 25
    assert len(page1) == 20
    assert len(page2) == 5
    assert page1[0] is not None and page1[0].name == "dept-00"
    assert page2[0] is not None and page2[0].name == "dept-20"


async def test_department_update_moves_and_restores(
    departments: PostgresDepartmentRepository,
) -> None:
    root_id = uuid7()
    child_id = uuid7()
    await departments.add(TENANT_A, root_id, "Company", None)
    await departments.add(TENANT_A, child_id, "Engineering", root_id)

    moved = await departments.update(TENANT_A, child_id, parent_department_id=None)
    assert moved is not None and moved.parent_department_id is None

    archived = await departments.update(TENANT_A, child_id, archived_at=ARCHIVED_AT)
    assert archived is not None and archived.archived_at == ARCHIVED_AT

    restored = await departments.update(TENANT_A, child_id, archived_at=None)
    assert restored is not None and restored.archived_at is None


async def test_department_update_missing_row_returns_none(
    departments: PostgresDepartmentRepository,
) -> None:
    assert await departments.update(TENANT_A, uuid7(), name="Ghost") is None


async def test_department_ancestor_ids(
    departments: PostgresDepartmentRepository,
) -> None:
    root_id, mid_id, leaf_id = uuid7(), uuid7(), uuid7()
    await departments.add(TENANT_A, root_id, "Company", None)
    await departments.add(TENANT_A, mid_id, "Engineering", root_id)
    await departments.add(TENANT_A, leaf_id, "Backend", mid_id)

    ancestors = await departments.ancestor_ids(TENANT_A, leaf_id)

    assert list(ancestors) == [mid_id, root_id]
    assert await departments.ancestor_ids(TENANT_A, root_id) == []
    # Tenant-scoped walk: B sees no ancestors for A's department.
    assert await departments.ancestor_ids(TENANT_B, leaf_id) == []


async def test_department_duplicate_insert_maps_to_taken(
    departments: PostgresDepartmentRepository, session: AsyncSession
) -> None:
    await departments.add(TENANT_A, uuid7(), "Engineering", None)

    with pytest.raises(DepartmentNameTakenError):
        await departments.add(TENANT_A, uuid7(), "Engineering", None)

    await session.rollback()


async def test_position_scoping_filters_and_duplicate(
    positions: PostgresPositionRepository, session: AsyncSession
) -> None:
    id_a = uuid7()
    await positions.add(TENANT_A, id_a, "Senior Nurse", "Ward lead")
    await positions.add(TENANT_A, uuid7(), "Junior Nurse", None)
    archived_id = uuid7()
    await positions.add(TENANT_A, archived_id, "Retired Doctor", None)
    await positions.update(TENANT_A, archived_id, archived_at=ARCHIVED_AT)
    await positions.add(TENANT_B, uuid7(), "Senior Nurse", None)

    assert (await positions.get(TENANT_B, id_a)) is None

    items, total = await positions.list(
        TENANT_A, PositionFilters(q="nurse"), PageParams()
    )
    assert total == 2
    assert [item.title for item in items] == ["Junior Nurse", "Senior Nurse"]

    _, total_b = await positions.list(TENANT_B, PositionFilters(), PageParams())
    assert total_b == 1

    with pytest.raises(PositionTitleTakenError):
        await positions.add(TENANT_A, uuid7(), "Senior Nurse", None)
    await session.rollback()


async def test_location_scoping_filters_and_duplicate(
    locations: PostgresLocationRepository, session: AsyncSession
) -> None:
    id_a = uuid7()
    await locations.add(TENANT_A, id_a, "HQ", "1 Main St", "Asia/Ho_Chi_Minh")
    archived_id = uuid7()
    await locations.add(TENANT_A, archived_id, "Old Office", None, "UTC")
    await locations.update(TENANT_A, archived_id, archived_at=ARCHIVED_AT)
    await locations.add(TENANT_B, uuid7(), "HQ", None, "UTC")

    assert (await locations.get(TENANT_B, id_a)) is None

    items, total = await locations.list(
        TENANT_A, LocationFilters(q="hq", include_archived=True), PageParams()
    )
    assert total == 1 and items[0] is not None and items[0].name == "HQ"

    _, total_archived = await locations.list(
        TENANT_A, LocationFilters(include_archived=True), PageParams()
    )
    assert total_archived == 2

    with pytest.raises(LocationNameTakenError):
        await locations.add(TENANT_A, uuid7(), "HQ", None, "UTC")
    await session.rollback()


async def test_uuid7_ids_are_stdlib_uuids(
    departments: PostgresDepartmentRepository,
) -> None:
    new_id = uuid7()
    assert isinstance(new_id, UUID)

    created = await departments.add(TENANT_A, new_id, "Engineering", None)
    assert created.id == new_id
