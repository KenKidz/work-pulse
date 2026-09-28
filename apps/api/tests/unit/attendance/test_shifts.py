"""Unit tests for `ShiftService` — scope uniqueness, windows, references, audit."""

from datetime import UTC, datetime, time
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.attendance.exceptions import (
    ShiftNotFoundError,
    ShiftScopeTakenError,
    ShiftTimeInvalidError,
)
from app.domains.attendance.schemas import ShiftCreate, ShiftUpdate
from app.domains.attendance.service import ShiftService
from app.domains.organizations.exceptions import LocationNotFoundError
from tests.unit.attendance.conftest import (
    LOCATION,
    LOCATION_B,
    NOW,
    TENANT_A,
    TENANT_B,
    identity,
    seed_location,
    seed_shift,
)
from tests.unit.attendance.fakes import FakeShiftRepo
from tests.unit.organizations.fakes import FakeLocationRepo, RecordingAuditWriter


def shift_create(
    *,
    name: str = "Day",
    start_time: time = time(9, 0),
    end_time: time = time(18, 0),
    grace_minutes: int = 10,
    location_id: UUID | None = None,
) -> ShiftCreate:
    return ShiftCreate(
        name=name,
        start_time=start_time,
        end_time=end_time,
        grace_minutes=grace_minutes,
        location_id=location_id,
    )


async def test_create_shift_records_scope_and_audit(
    shift_service: ShiftService, audit: RecordingAuditWriter
) -> None:
    shift = await shift_service.create(identity(), shift_create(), NOW)

    assert shift.name == "Day"
    assert shift.start_time == time(9, 0)
    assert shift.end_time == time(18, 0)
    assert shift.grace_minutes == 10
    assert shift.location_id is None  # the tenant default

    entry = audit.entries[0]
    assert entry.action == "shift.created"
    assert entry.entity_type == "shift"
    assert entry.entity_id == shift.id
    assert entry.occurred_at == NOW
    assert entry.changes is not None
    assert entry.changes["start_time"] == {"old": None, "new": "09:00"}
    assert entry.changes["location_id"] == {"old": None, "new": None}


@pytest.mark.parametrize(
    ("start_time", "end_time"),
    [(time(9, 0), time(9, 0)), (time(9, 0), time(8, 0))],
)
async def test_create_shift_requires_end_after_start(
    shift_service: ShiftService,
    start_time: time,
    end_time: time,
) -> None:
    with pytest.raises(ShiftTimeInvalidError) as exc:
        await shift_service.create(
            identity(),
            shift_create(start_time=start_time, end_time=end_time),
            NOW,
        )

    assert exc.value.code == "shift_time_invalid"
    assert exc.value.status_code == 422


async def test_shift_scope_is_unique_per_location_and_for_the_default(
    shift_service: ShiftService,
    shift_repo: FakeShiftRepo,
    location_repo: FakeLocationRepo,
) -> None:
    await seed_location(location_repo)
    await seed_shift(shift_repo, location_id=LOCATION)  # location scope taken
    await seed_shift(shift_repo)  # tenant default taken

    with pytest.raises(ShiftScopeTakenError) as exc:
        await shift_service.create(
            identity(), shift_create(name="Other", location_id=LOCATION), NOW
        )
    assert exc.value.code == "shift_scope_taken"

    with pytest.raises(ShiftScopeTakenError):
        await shift_service.create(identity(), shift_create(name="Other"), NOW)


async def test_create_shift_requires_a_location_in_the_tenant(
    shift_service: ShiftService, location_repo: FakeLocationRepo
) -> None:
    with pytest.raises(LocationNotFoundError) as exc:  # unknown id
        await shift_service.create(identity(), shift_create(location_id=LOCATION), NOW)
    assert exc.value.code == "location_not_found"
    assert exc.value.status_code == 404

    await seed_location(location_repo, tenant_id=TENANT_B)  # exists, other tenant
    with pytest.raises(LocationNotFoundError):
        await shift_service.create(identity(), shift_create(location_id=LOCATION), NOW)


async def test_archived_location_is_still_referenceable(
    shift_service: ShiftService,
    location_repo: FakeLocationRepo,
) -> None:
    await seed_location(location_repo)
    await location_repo.update(
        TENANT_A, LOCATION, archived_at=datetime(2026, 9, 1, tzinfo=UTC)
    )

    shift = await shift_service.create(
        identity(), shift_create(location_id=LOCATION), NOW
    )

    assert shift.location_id == LOCATION  # T1: archived locations stay valid


async def test_location_scope_and_tenant_default_coexist(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    await seed_shift(shift_repo, location_id=LOCATION)

    default = await shift_service.create(identity(), shift_create(name="Default"), NOW)

    assert default.location_id is None


async def test_scope_uniqueness_is_tenant_scoped(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    await seed_shift(shift_repo, tenant_id=TENANT_B)

    shift = await shift_service.create(identity(), shift_create(), NOW)

    assert shift.tenant_id != TENANT_B


async def test_update_validates_the_merged_window(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    shift = await seed_shift(shift_repo)  # 09:00–18:00

    with pytest.raises(ShiftTimeInvalidError):
        await shift_service.update(
            identity(), shift.id, ShiftUpdate(end_time=time(8, 0)), NOW
        )
    with pytest.raises(ShiftTimeInvalidError):
        await shift_service.update(
            identity(), shift.id, ShiftUpdate(start_time=time(19, 0)), NOW
        )


async def test_update_to_a_taken_scope_is_a_conflict(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    scoped = await seed_shift(shift_repo, location_id=LOCATION)
    await seed_shift(shift_repo)  # tenant default

    with pytest.raises(ShiftScopeTakenError):
        await shift_service.update(
            identity(), scoped.id, ShiftUpdate(location_id=None), NOW
        )


async def test_update_shift_requires_a_location_in_the_tenant(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    shift = await seed_shift(shift_repo)  # tenant default

    with pytest.raises(LocationNotFoundError) as exc:
        await shift_service.update(
            identity(), shift.id, ShiftUpdate(location_id=LOCATION_B), NOW
        )

    assert exc.value.code == "location_not_found"


async def test_update_can_release_a_location_scope_to_the_default(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    shift = await seed_shift(shift_repo, location_id=LOCATION)

    updated = await shift_service.update(
        identity(), shift.id, ShiftUpdate(location_id=None), NOW
    )

    assert updated.location_id is None


async def test_update_audits_old_and_new_values(
    shift_service: ShiftService,
    shift_repo: FakeShiftRepo,
    audit: RecordingAuditWriter,
) -> None:
    shift = await seed_shift(shift_repo)  # name "Day", grace 10
    audit.entries.clear()

    updated = await shift_service.update(
        identity(), shift.id, ShiftUpdate(name="Night", grace_minutes=15), NOW
    )

    assert updated.name == "Night"
    entry = audit.entries[0]
    assert entry.action == "shift.updated"
    assert entry.changes is not None
    assert entry.changes["name"] == {"old": "Day", "new": "Night"}
    assert entry.changes["grace_minutes"] == {"old": 10, "new": 15}


async def test_update_with_no_fields_is_a_noop(
    shift_service: ShiftService,
    shift_repo: FakeShiftRepo,
    audit: RecordingAuditWriter,
) -> None:
    shift = await seed_shift(shift_repo)
    audit.entries.clear()

    result = await shift_service.update(identity(), shift.id, ShiftUpdate(), NOW)

    assert result == shift
    assert audit.entries == []


async def test_update_unknown_or_cross_tenant_shift_is_404(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    with pytest.raises(ShiftNotFoundError) as exc:
        await shift_service.update(identity(), uuid7(), ShiftUpdate(name="X"), NOW)
    assert exc.value.code == "shift_not_found"

    other_tenant = await seed_shift(shift_repo, tenant_id=TENANT_B)
    with pytest.raises(ShiftNotFoundError):
        await shift_service.update(
            identity(), other_tenant.id, ShiftUpdate(name="X"), NOW
        )


async def test_list_orders_location_scopes_before_the_tenant_default(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    await seed_shift(shift_repo, name="Zulu", location_id=LOCATION)
    await seed_shift(shift_repo, name="Alpha")  # tenant default

    shifts = await shift_service.list(identity())

    assert [shift.name for shift in shifts] == ["Zulu", "Alpha"]


async def test_list_is_tenant_scoped(
    shift_service: ShiftService, shift_repo: FakeShiftRepo
) -> None:
    await seed_shift(shift_repo, tenant_id=TENANT_B)

    assert await shift_service.list(identity()) == []
