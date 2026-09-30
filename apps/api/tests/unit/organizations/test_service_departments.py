"""Unit tests for DepartmentService — happy paths, failures, audit behavior."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domains.organizations.exceptions import (
    DepartmentArchivedInUseError,
    DepartmentCycleError,
    DepartmentNameTakenError,
    DepartmentNotFoundError,
)
from app.domains.organizations.repository import DepartmentFilters
from app.domains.organizations.schemas import (
    ArchiveRequest,
    DepartmentCreate,
    DepartmentUpdate,
)
from app.domains.organizations.service import DepartmentService
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import (
    FakeDepartmentRepo,
    FakeDepartmentUsage,
    RecordingAuditWriter,
)

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
ACTOR = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def identity(tenant_id: UUID = TENANT_A, role: Role = Role.HR) -> Identity:
    return Identity(tenant_id=tenant_id, actor_id=ACTOR, role=role)


@pytest.fixture
def repo() -> FakeDepartmentRepo:
    return FakeDepartmentRepo()


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def usage() -> FakeDepartmentUsage:
    return FakeDepartmentUsage(count=0)


@pytest.fixture
def service(
    repo: FakeDepartmentRepo, audit: RecordingAuditWriter, usage: FakeDepartmentUsage
) -> DepartmentService:
    return DepartmentService(repo=repo, audit=audit, usage=usage)


async def test_create_returns_department_and_audits(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )

    assert created.name == "Engineering"
    assert created.tenant_id == TENANT_A
    assert created.parent_department_id is None
    assert created.archived_at is None

    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry is not None
    assert entry.action == "department.created"
    assert entry.entity_type == "department"
    assert entry.entity_id == created.id
    assert entry.actor_id == ACTOR
    assert entry.occurred_at == NOW
    assert entry.changes == {
        "name": {"old": None, "new": "Engineering"},
        "parent_department_id": {"old": None, "new": None},
    }


async def test_create_with_parent(service: DepartmentService) -> None:
    parent = await service.create(identity(), DepartmentCreate(name="Company"), NOW)
    child = await service.create(
        identity(),
        DepartmentCreate(name="Backend", parent_department_id=parent.id),
        NOW,
    )

    assert child.parent_department_id == parent.id


async def test_create_duplicate_name_raises_taken(service: DepartmentService) -> None:
    await service.create(identity(), DepartmentCreate(name="Engineering"), NOW)

    with pytest.raises(DepartmentNameTakenError):
        await service.create(identity(), DepartmentCreate(name="Engineering"), NOW)


async def test_duplicate_name_allowed_in_other_tenant(
    service: DepartmentService,
) -> None:
    await service.create(identity(TENANT_A), DepartmentCreate(name="Engineering"), NOW)
    other = await service.create(
        identity(TENANT_B), DepartmentCreate(name="Engineering"), NOW
    )

    assert other.tenant_id == TENANT_B


async def test_create_with_unknown_parent_raises_not_found(
    service: DepartmentService,
) -> None:
    with pytest.raises(DepartmentNotFoundError):
        await service.create(
            identity(),
            DepartmentCreate(
                name="Orphan",
                parent_department_id=UUID("00000000-0000-4000-8000-00000000dead"),
            ),
            NOW,
        )


async def test_create_with_parent_from_other_tenant_raises_not_found(
    service: DepartmentService,
) -> None:
    foreign = await service.create(
        identity(TENANT_B), DepartmentCreate(name="Foreign"), NOW
    )

    with pytest.raises(DepartmentNotFoundError):
        await service.create(
            identity(TENANT_A),
            DepartmentCreate(name="Sneaky", parent_department_id=foreign.id),
            NOW,
        )


async def test_get_unknown_id_raises_not_found(service: DepartmentService) -> None:
    with pytest.raises(DepartmentNotFoundError):
        await service.get(identity(), UUID("00000000-0000-4000-8000-00000000dead"))


async def test_get_cross_tenant_raises_not_found(service: DepartmentService) -> None:
    created = await service.create(
        identity(TENANT_A), DepartmentCreate(name="Engineering"), NOW
    )

    with pytest.raises(DepartmentNotFoundError):
        await service.get(identity(TENANT_B), created.id)


async def test_list_returns_items_and_page(service: DepartmentService) -> None:
    await service.create(identity(), DepartmentCreate(name="Engineering"), NOW)
    await service.create(identity(), DepartmentCreate(name="People"), NOW)

    page = await service.list(
        identity(), DepartmentFilters(), PageParams(page=1, page_size=20)
    )

    assert [item.name for item in page.items] == ["Engineering", "People"]
    assert page.page.total_items == 2
    assert page.page.total_pages == 1


async def test_update_rename_audits_old_and_new(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    audit.entries.clear()

    updated = await service.update(
        identity(), created.id, DepartmentUpdate(name="R&D"), NOW
    )

    assert updated.name == "R&D"
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry is not None
    assert entry.action == "department.updated"
    assert entry.changes == {"name": {"old": "Engineering", "new": "R&D"}}


async def test_update_to_duplicate_name_raises_taken(
    service: DepartmentService,
) -> None:
    await service.create(identity(), DepartmentCreate(name="Engineering"), NOW)
    people = await service.create(identity(), DepartmentCreate(name="People"), NOW)

    with pytest.raises(DepartmentNameTakenError):
        await service.update(
            identity(), people.id, DepartmentUpdate(name="Engineering"), NOW
        )


async def test_update_same_name_is_allowed(service: DepartmentService) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )

    updated = await service.update(
        identity(), created.id, DepartmentUpdate(name="Engineering"), NOW
    )

    assert updated.name == "Engineering"


async def test_update_empty_patch_is_a_noop_without_audit(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    audit.entries.clear()

    updated = await service.update(identity(), created.id, DepartmentUpdate(), NOW)

    assert updated.id == created.id
    assert audit.entries == []


async def test_update_self_parent_raises_cycle(service: DepartmentService) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )

    with pytest.raises(DepartmentCycleError):
        await service.update(
            identity(),
            created.id,
            DepartmentUpdate(parent_department_id=created.id),
            NOW,
        )


async def test_update_descendant_parent_raises_cycle(
    service: DepartmentService,
) -> None:
    root = await service.create(identity(), DepartmentCreate(name="Company"), NOW)
    child = await service.create(
        identity(),
        DepartmentCreate(name="Engineering", parent_department_id=root.id),
        NOW,
    )
    grandchild = await service.create(
        identity(), DepartmentCreate(name="Backend", parent_department_id=child.id), NOW
    )

    with pytest.raises(DepartmentCycleError):
        await service.update(
            identity(),
            root.id,
            DepartmentUpdate(parent_department_id=grandchild.id),
            NOW,
        )


async def test_update_reparent_to_root_with_explicit_null(
    service: DepartmentService,
) -> None:
    root = await service.create(identity(), DepartmentCreate(name="Company"), NOW)
    child = await service.create(
        identity(),
        DepartmentCreate(name="Engineering", parent_department_id=root.id),
        NOW,
    )

    updated = await service.update(
        identity(),
        child.id,
        DepartmentUpdate.model_validate({"parent_department_id": None}),
        NOW,
    )

    assert updated.parent_department_id is None


async def test_update_parent_to_unknown_raises_not_found(
    service: DepartmentService,
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )

    with pytest.raises(DepartmentNotFoundError):
        await service.update(
            identity(),
            created.id,
            DepartmentUpdate(
                parent_department_id=UUID("00000000-0000-4000-8000-00000000dead")
            ),
            NOW,
        )


async def test_archive_sets_archived_at_and_audits_reason(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    audit.entries.clear()

    archived = await service.archive(
        identity(), created.id, ArchiveRequest(reason="reorg"), NOW
    )

    assert archived.archived_at == NOW
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry is not None
    assert entry.action == "department.archived"
    assert entry.reason == "reorg"


async def test_archive_blocked_while_in_use(
    service: DepartmentService, usage: FakeDepartmentUsage, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    usage.count = 3

    with pytest.raises(DepartmentArchivedInUseError):
        await service.archive(identity(), created.id, None, NOW)

    assert usage.calls == [(TENANT_A, created.id)]
    assert audit.entries[-1] is not None
    assert audit.entries[-1].action == "department.created"  # nothing new was written


async def test_archive_of_archived_is_noop_without_audit(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    await service.archive(identity(), created.id, None, NOW)
    audit.entries.clear()

    again = await service.archive(
        identity(), created.id, ArchiveRequest(reason="x"), NOW
    )

    assert again.archived_at == NOW
    assert audit.entries == []


async def test_restore_clears_archived_at_and_audits(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    await service.archive(identity(), created.id, None, NOW)
    audit.entries.clear()

    restored = await service.restore(identity(), created.id, NOW)

    assert restored.archived_at is None
    assert len(audit.entries) == 1
    assert audit.entries[0] is not None
    assert audit.entries[0].action == "department.restored"


async def test_restore_of_active_is_noop_without_audit(
    service: DepartmentService, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(), DepartmentCreate(name="Engineering"), NOW
    )
    audit.entries.clear()

    result = await service.restore(identity(), created.id, NOW)

    assert result.archived_at is None
    assert audit.entries == []
