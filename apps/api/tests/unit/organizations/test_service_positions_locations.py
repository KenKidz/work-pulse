"""Unit tests for PositionService and LocationService."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domains.organizations.exceptions import (
    InvalidTimezoneError,
    LocationNameTakenError,
    LocationNotFoundError,
    PositionNotFoundError,
    PositionTitleTakenError,
)
from app.domains.organizations.repository import LocationFilters, PositionFilters
from app.domains.organizations.schemas import (
    ArchiveRequest,
    LocationCreate,
    LocationUpdate,
    PositionCreate,
    PositionUpdate,
)
from app.domains.organizations.service import LocationService, PositionService
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import (
    FakeLocationRepo,
    FakePositionRepo,
    RecordingAuditWriter,
)

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
IDENTITY = Identity(tenant_id=TENANT_A, actor_id=ACTOR, role=Role.HR)


@pytest.fixture
def position_repo() -> FakePositionRepo:
    return FakePositionRepo()


@pytest.fixture
def position_audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def positions(
    position_repo: FakePositionRepo, position_audit: RecordingAuditWriter
) -> PositionService:
    return PositionService(repo=position_repo, audit=position_audit)


@pytest.fixture
def location_repo() -> FakeLocationRepo:
    return FakeLocationRepo()


@pytest.fixture
def location_audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def locations(
    location_repo: FakeLocationRepo, location_audit: RecordingAuditWriter
) -> LocationService:
    return LocationService(repo=location_repo, audit=location_audit)


# --- Positions ---------------------------------------------------------------


async def test_position_lifecycle_audits_each_transition(
    positions: PositionService, position_audit: RecordingAuditWriter
) -> None:
    created = await positions.create(
        IDENTITY, PositionCreate(title="Senior Nurse", description="Ward lead"), NOW
    )
    assert created.title == "Senior Nurse"
    assert created.description == "Ward lead"

    fetched = await positions.get(IDENTITY, created.id)
    assert fetched.id == created.id

    page = await positions.list(IDENTITY, PositionFilters(q="nurse"), PageParams())
    assert page.page.total_items == 1

    updated = await positions.update(
        IDENTITY, created.id, PositionUpdate(title="Nurse Lead"), NOW
    )
    assert updated.title == "Nurse Lead"

    archived = await positions.archive(
        IDENTITY, created.id, ArchiveRequest(reason="restructure"), NOW
    )
    assert archived.archived_at == NOW

    restored = await positions.restore(IDENTITY, created.id, NOW)
    assert restored.archived_at is None

    assert [entry.action for entry in position_audit.entries] == [
        "position.created",
        "position.updated",
        "position.archived",
        "position.restored",
    ]


async def test_position_duplicate_title_raises_taken(
    positions: PositionService,
) -> None:
    await positions.create(IDENTITY, PositionCreate(title="Senior Nurse"), NOW)

    with pytest.raises(PositionTitleTakenError):
        await positions.create(IDENTITY, PositionCreate(title="Senior Nurse"), NOW)


async def test_position_update_to_duplicate_title_raises_taken(
    positions: PositionService,
) -> None:
    await positions.create(IDENTITY, PositionCreate(title="Senior Nurse"), NOW)
    other = await positions.create(IDENTITY, PositionCreate(title="Junior Nurse"), NOW)

    with pytest.raises(PositionTitleTakenError):
        await positions.update(
            IDENTITY, other.id, PositionUpdate(title="Senior Nurse"), NOW
        )


async def test_position_get_unknown_raises_not_found(
    positions: PositionService,
) -> None:
    with pytest.raises(PositionNotFoundError):
        await positions.get(IDENTITY, UUID("00000000-0000-4000-8000-00000000dead"))


async def test_position_archive_noop_writes_no_audit(
    positions: PositionService, position_audit: RecordingAuditWriter
) -> None:
    created = await positions.create(
        IDENTITY, PositionCreate(title="Senior Nurse"), NOW
    )
    await positions.archive(IDENTITY, created.id, None, NOW)
    position_audit.entries.clear()

    again = await positions.archive(IDENTITY, created.id, None, NOW)

    assert again.archived_at == NOW
    assert position_audit.entries == []

    await positions.restore(IDENTITY, created.id, NOW)

    assert [entry.action for entry in position_audit.entries] == ["position.restored"]


# --- Locations ---------------------------------------------------------------


async def test_location_lifecycle_and_timezone(locations: LocationService) -> None:
    created = await locations.create(
        IDENTITY,
        LocationCreate(name="HQ", address="1 Main St", timezone="Asia/Ho_Chi_Minh"),
        NOW,
    )
    assert created.timezone == "Asia/Ho_Chi_Minh"

    page = await locations.list(IDENTITY, LocationFilters(), PageParams())
    assert page.page.total_items == 1

    updated = await locations.update(
        IDENTITY, created.id, LocationUpdate(timezone="Europe/Berlin"), NOW
    )
    assert updated.timezone == "Europe/Berlin"

    archived = await locations.archive(IDENTITY, created.id, None, NOW)
    assert archived.archived_at == NOW

    restored = await locations.restore(IDENTITY, created.id, NOW)
    assert restored.archived_at is None


async def test_location_invalid_timezone_on_create_raises(
    locations: LocationService,
) -> None:
    with pytest.raises(InvalidTimezoneError):
        await locations.create(
            IDENTITY,
            LocationCreate(name="Mars Base", timezone="Mars/Olympus_Mons"),
            NOW,
        )


async def test_location_invalid_timezone_on_update_raises(
    locations: LocationService,
) -> None:
    created = await locations.create(
        IDENTITY, LocationCreate(name="HQ", timezone="Asia/Ho_Chi_Minh"), NOW
    )

    with pytest.raises(InvalidTimezoneError):
        await locations.update(
            IDENTITY, created.id, LocationUpdate(timezone="Not/AZone"), NOW
        )


async def test_location_duplicate_name_raises_taken(locations: LocationService) -> None:
    await locations.create(IDENTITY, LocationCreate(name="HQ", timezone="UTC"), NOW)

    with pytest.raises(LocationNameTakenError):
        await locations.create(IDENTITY, LocationCreate(name="HQ", timezone="UTC"), NOW)


async def test_location_get_unknown_raises_not_found(
    locations: LocationService,
) -> None:
    with pytest.raises(LocationNotFoundError):
        await locations.get(IDENTITY, UUID("00000000-0000-4000-8000-00000000dead"))
