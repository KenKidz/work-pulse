"""Unit tests for `SkillService` — the pinned skill rules (T7).

Every call passes an explicit `NOW`; the service never reads the wall clock
(`.ai/testing.md`: no wall-time assertions). The fake repository enforces the
same tenant scope, catalog uniqueness (archived rows included), archive no-ops
and employee-skill uniqueness as the Postgres adapter, so these tests exercise
business logic, not I/O.

Covered here (`.ai/features/performance.md` → T7, acceptance criterion 6):
catalog creation/rename/archive/restore (no-op repeats are silent), name
uniqueness across archived rows, `q`/`include_archived` listings, employee
skill attach/update/remove (`409 skill_archived`, `409 employee_skill_exists`),
role/scope checks (Employee/Manager/Finance), tenant isolation, and the
audit-only PII rule (no events; notes and descriptions are markers only).
"""

import json
from uuid import UUID

import pytest

from app.domains.performance.exceptions import (
    EmployeeNotFoundError,
    EmployeeSkillExistsError,
    PerformanceDeniedError,
    SkillArchivedError,
    SkillNameTakenError,
    SkillNotFoundError,
)
from app.domains.performance.schemas import (
    EmployeeSkillCreate,
    EmployeeSkillUpdate,
    Skill,
    SkillCreate,
    SkillUpdate,
)
from app.domains.performance.service import SkillService
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.performance.conftest import (
    EMPLOYEE,
    MANAGER,
    NOW,
    OTHER,
    PEER,
    REPORT,
    TENANT_A,
    TENANT_B,
    identity,
    seed_employee,
)
from tests.unit.performance.fakes import FakeEmployeeScope, FakePerformanceRepo

PII_SKILL_DESCRIPTION = "Internal calibration notes — pii-skill-description"
PII_NOTE = "Confidential proficiency note — pii-employee-skill-note"
UNKNOWN_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000ee")


@pytest.fixture
def service(
    repo: FakePerformanceRepo,
    scope: FakeEmployeeScope,
    audit: RecordingAuditWriter,
) -> SkillService:
    return SkillService(repo=repo, scope=scope, audit=audit)


def _hr(tenant_id: UUID = TENANT_A) -> Identity:
    return identity(tenant_id=tenant_id, role=Role.HR, employee_id=None)


def _manager() -> Identity:
    return identity(role=Role.MANAGER, employee_id=MANAGER)


def _world(repo: FakePerformanceRepo) -> None:
    """Five active employees with one direct report."""
    seed_employee(repo, employee_id=EMPLOYEE)
    seed_employee(repo, employee_id=MANAGER)
    seed_employee(repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(repo, employee_id=PEER)
    seed_employee(repo, employee_id=OTHER)


async def _create_skill(
    service: SkillService,
    *,
    caller: Identity | None = None,
    name: str = "Python",
    description: str | None = None,
) -> Skill:
    return await service.create_skill(
        caller if caller is not None else _hr(),
        SkillCreate(name=name, description=description),
        NOW,
    )


# --- catalog -----------------------------------------------------------------


async def test_create_skill_audits_without_pii(
    service: SkillService, audit: RecordingAuditWriter
) -> None:
    skill = await _create_skill(service, description=PII_SKILL_DESCRIPTION)

    assert skill.name == "Python"
    assert skill.description == PII_SKILL_DESCRIPTION
    assert skill.archived_at is None

    [entry] = audit.entries
    assert entry.action == "skill.created"
    assert entry.entity_type == "skill"
    assert entry.entity_id == skill.id
    assert entry.changes is not None
    assert entry.changes["name"] == {"old": None, "new": "Python"}
    assert entry.changes["description"] == "changed"  # PII: marker only
    assert PII_SKILL_DESCRIPTION not in json.dumps(entry.changes)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_catalog_mutations_require_management(
    service: SkillService,
    repo: FakePerformanceRepo,
    role: Role,
) -> None:
    _world(repo)
    caller = identity(role=role)
    skill = await _create_skill(service)

    with pytest.raises(PerformanceDeniedError):
        await service.create_skill(caller, SkillCreate(name="Go"), NOW)
    with pytest.raises(PerformanceDeniedError):
        await service.update_skill(caller, skill.id, SkillUpdate(name="Rust"), NOW)
    with pytest.raises(PerformanceDeniedError):
        await service.archive_skill(caller, skill.id, NOW)
    with pytest.raises(PerformanceDeniedError):
        await service.restore_skill(caller, skill.id, NOW)


async def test_duplicate_name_spans_archived_rows(
    service: SkillService, repo: FakePerformanceRepo
) -> None:
    python = await _create_skill(service)
    go = await _create_skill(service, name="Go")
    await service.archive_skill(_hr(), python.id, NOW)

    with pytest.raises(SkillNameTakenError):
        await service.create_skill(_hr(), SkillCreate(name="Python"), NOW)
    with pytest.raises(SkillNameTakenError):
        await service.update_skill(_hr(), go.id, SkillUpdate(name="Python"), NOW)


async def test_update_skill_audits_and_empty_update_is_silent(
    service: SkillService, audit: RecordingAuditWriter
) -> None:
    skill = await _create_skill(service)
    entries_after_create = len(audit.entries)

    updated = await service.update_skill(
        _hr(),
        skill.id,
        SkillUpdate(name="Python 3", description=PII_SKILL_DESCRIPTION),
        NOW,
    )
    noop = await service.update_skill(_hr(), skill.id, SkillUpdate(), NOW)

    assert updated.name == "Python 3"
    assert updated.description == PII_SKILL_DESCRIPTION
    assert noop == updated
    assert len(audit.entries) == entries_after_create + 1
    [entry] = audit.entries[entries_after_create:]
    assert entry.action == "skill.updated"
    assert entry.changes is not None
    assert entry.changes["name"] == {"old": "Python", "new": "Python 3"}
    assert entry.changes["description"] == "changed"
    assert PII_SKILL_DESCRIPTION not in json.dumps(entry.changes)

    with pytest.raises(SkillNotFoundError):
        await service.update_skill(_hr(), OTHER, SkillUpdate(name="Ghost"), NOW)


async def test_archive_restore_noops_and_listing_filters(
    service: SkillService, audit: RecordingAuditWriter
) -> None:
    python = await _create_skill(service)
    await _create_skill(service, name="Go")
    page = PageParams()

    archived = await service.archive_skill(_hr(), python.id, NOW)
    assert archived.archived_at == NOW
    [entry] = audit.entries[-1:]
    assert entry.action == "skill.archived"
    assert entry.changes == {"archived_at": {"old": None, "new": NOW.isoformat()}}
    entries_after_archive = len(audit.entries)

    # Already archived: 200-equivalent no-op, no audit entry.
    assert (await service.archive_skill(_hr(), python.id, NOW)).archived_at == NOW
    assert len(audit.entries) == entries_after_archive

    default = await service.list_skills(identity(role=Role.EMPLOYEE), page=page)
    assert [item.name for item in default.items] == ["Go"]
    with_archived = await service.list_skills(
        identity(role=Role.EMPLOYEE), include_archived=True, page=page
    )
    assert [item.name for item in with_archived.items] == ["Go", "Python"]

    searched = await service.list_skills(
        identity(role=Role.EMPLOYEE),
        q="PY",
        include_archived=True,
        page=page,
    )
    assert [item.id for item in searched.items] == [python.id]

    restored = await service.restore_skill(_hr(), python.id, NOW)
    assert restored.archived_at is None
    [entry] = audit.entries[-1:]
    assert entry.action == "skill.restored"
    assert entry.changes == {"archived_at": {"old": NOW.isoformat(), "new": None}}
    entries_after_restore = len(audit.entries)
    await service.restore_skill(_hr(), python.id, NOW)
    assert len(audit.entries) == entries_after_restore

    with pytest.raises(SkillNotFoundError):
        await service.archive_skill(_hr(), OTHER, NOW)
    with pytest.raises(SkillNotFoundError):
        await service.restore_skill(_hr(), OTHER, NOW)


async def test_catalog_is_tenant_isolated(
    service: SkillService, repo: FakePerformanceRepo
) -> None:
    skill = await _create_skill(service)
    tenant_b = _hr(TENANT_B)

    with pytest.raises(SkillNotFoundError):
        await service.update_skill(tenant_b, skill.id, SkillUpdate(name="Stolen"), NOW)
    with pytest.raises(SkillNotFoundError):
        await service.archive_skill(tenant_b, skill.id, NOW)
    with pytest.raises(SkillNotFoundError):
        await service.restore_skill(tenant_b, skill.id, NOW)

    page = PageParams()
    assert (await service.list_skills(tenant_b, page=page)).items == []
    # A tenant-B rename of the same name is not a conflict.
    other = await service.create_skill(tenant_b, SkillCreate(name="Python"), NOW)
    assert other.name == "Python"
    assert (await service.list_skills(_hr(), page=page)).items[0].id == skill.id


# --- employee skills ---------------------------------------------------------


async def test_record_employee_skill_audits_without_pii(
    service: SkillService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
) -> None:
    _world(repo)
    skill = await _create_skill(service)
    entries_after_create = len(audit.entries)

    row = await service.record_employee_skill(
        _hr(),
        REPORT,
        EmployeeSkillCreate(
            skill_id=skill.id,
            proficiency=4,
            note=PII_NOTE,
        ),
        NOW,
    )

    assert row.skill_id == skill.id
    assert row.skill_name == "Python"
    assert row.proficiency == 4
    assert row.note == PII_NOTE

    [entry] = audit.entries[entries_after_create:]
    assert entry.action == "employee_skill.recorded"
    assert entry.entity_type == "employee_skill"
    assert entry.entity_id == skill.id
    assert entry.changes is not None
    assert entry.changes["employee_id"] == {"old": None, "new": str(REPORT)}
    assert entry.changes["skill_id"] == {"old": None, "new": str(skill.id)}
    assert entry.changes["proficiency"] == {"old": None, "new": 4}
    assert entry.changes["note"] == "changed"  # PII: marker only
    assert PII_NOTE not in json.dumps(entry.changes)


async def test_record_employee_skill_rejects_duplicates_archived_and_foreign(
    service: SkillService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    skill = await _create_skill(service)
    payload = EmployeeSkillCreate(skill_id=skill.id, proficiency=3)

    await service.record_employee_skill(_hr(), EMPLOYEE, payload, NOW)
    with pytest.raises(EmployeeSkillExistsError):
        await service.record_employee_skill(_hr(), EMPLOYEE, payload, NOW)

    await service.archive_skill(_hr(), skill.id, NOW)
    with pytest.raises(SkillArchivedError):
        await service.record_employee_skill(
            _hr(),
            PEER,
            EmployeeSkillCreate(skill_id=skill.id, proficiency=3),
            NOW,
        )

    with pytest.raises(SkillNotFoundError):
        await service.record_employee_skill(
            _hr(),
            PEER,
            EmployeeSkillCreate(skill_id=OTHER, proficiency=3),
            NOW,
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.record_employee_skill(
            _hr(),
            UNKNOWN_EMPLOYEE,
            EmployeeSkillCreate(skill_id=skill.id, proficiency=3),
            NOW,
        )


async def test_employee_skill_scope_and_roles(
    service: SkillService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    skill = await _create_skill(service)
    for employee_id in (EMPLOYEE, MANAGER, REPORT, PEER):
        await service.record_employee_skill(
            _hr(),
            employee_id,
            EmployeeSkillCreate(skill_id=skill.id, proficiency=2),
            NOW,
        )

    async def _listed(caller: Identity, employee_id: UUID) -> list[UUID]:
        rows = await service.list_employee_skills(caller, employee_id)
        return [row.skill_id for row in rows.items]

    assert await _listed(identity(role=Role.EMPLOYEE), EMPLOYEE) == [skill.id]
    with pytest.raises(EmployeeNotFoundError):
        await service.list_employee_skills(identity(role=Role.EMPLOYEE), REPORT)
    with pytest.raises(EmployeeNotFoundError):
        await service.list_employee_skills(identity(role=Role.EMPLOYEE), PEER)

    assert await _listed(_manager(), MANAGER) == [skill.id]
    assert await _listed(_manager(), REPORT) == [skill.id]
    with pytest.raises(EmployeeNotFoundError):
        await service.list_employee_skills(_manager(), PEER)

    with pytest.raises(PerformanceDeniedError):
        await service.record_employee_skill(
            identity(role=Role.FINANCE),
            EMPLOYEE,
            EmployeeSkillCreate(skill_id=skill.id, proficiency=1),
            NOW,
        )
    with pytest.raises(PerformanceDeniedError):
        await service.remove_employee_skill(
            identity(role=Role.FINANCE), EMPLOYEE, skill.id, NOW
        )

    # Employee self-service and manager team rights; a colleague is out of scope.
    go = await _create_skill(service, name="Go")
    self_row = await service.record_employee_skill(
        identity(role=Role.EMPLOYEE),
        EMPLOYEE,
        EmployeeSkillCreate(skill_id=go.id, proficiency=5),
        NOW,
    )
    assert self_row.skill_name == "Go"

    report_row = await service.record_employee_skill(
        _manager(),
        REPORT,
        EmployeeSkillCreate(skill_id=go.id, proficiency=5),
        NOW,
    )
    assert report_row.skill_name == "Go"

    with pytest.raises(EmployeeNotFoundError):
        await service.record_employee_skill(
            identity(role=Role.EMPLOYEE),
            REPORT,
            EmployeeSkillCreate(skill_id=go.id, proficiency=1),
            NOW,
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.record_employee_skill(
            _manager(),
            PEER,
            EmployeeSkillCreate(skill_id=go.id, proficiency=1),
            NOW,
        )


async def test_update_and_remove_employee_skill_are_audited(
    service: SkillService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
) -> None:
    _world(repo)
    skill = await _create_skill(service)
    await service.record_employee_skill(
        _hr(),
        EMPLOYEE,
        EmployeeSkillCreate(skill_id=skill.id, proficiency=2, note=PII_NOTE),
        NOW,
    )
    entries_after_record = len(audit.entries)

    updated = await service.update_employee_skill(
        identity(role=Role.EMPLOYEE),
        EMPLOYEE,
        skill.id,
        EmployeeSkillUpdate(proficiency=5, note="Sharp — pii-employee-skill-note-2"),
        NOW,
    )

    assert updated.proficiency == 5
    [entry] = audit.entries[entries_after_record:]
    assert entry.action == "employee_skill.updated"
    assert entry.entity_type == "employee_skill"
    assert entry.changes is not None
    assert entry.changes["proficiency"] == {"old": 2, "new": 5}
    assert entry.changes["note"] == "changed"
    assert "Sharp" not in json.dumps(entry.changes)

    entries_after_update = len(audit.entries)
    # Clearing the note still records the marker, never the value.
    await service.update_employee_skill(
        _hr(),
        EMPLOYEE,
        skill.id,
        EmployeeSkillUpdate(proficiency=5),
        NOW,
    )
    [entry] = audit.entries[entries_after_update:]
    assert entry.changes == {"proficiency": {"old": 5, "new": 5}}

    # Archived skills keep their existing rows editable (the ADR 0007
    # divergence): only new assignment is blocked.
    await service.archive_skill(_hr(), skill.id, NOW)
    archived_update = await service.update_employee_skill(
        _hr(),
        EMPLOYEE,
        skill.id,
        EmployeeSkillUpdate(proficiency=4),
        NOW,
    )
    assert archived_update.proficiency == 4

    entries_before_remove = len(audit.entries)
    await service.remove_employee_skill(_hr(), EMPLOYEE, skill.id, NOW)
    [entry] = audit.entries[entries_before_remove:]
    assert entry.action == "employee_skill.removed"
    assert entry.entity_type == "employee_skill"
    assert entry.changes == {
        "employee_id": {"old": str(EMPLOYEE), "new": None},
        "skill_id": {"old": str(skill.id), "new": None},
        "proficiency": {"old": 4, "new": None},
    }
    assert (await service.list_employee_skills(_hr(), EMPLOYEE)).items == []

    with pytest.raises(SkillNotFoundError):
        await service.remove_employee_skill(_hr(), EMPLOYEE, skill.id, NOW)
    with pytest.raises(SkillNotFoundError):
        await service.update_employee_skill(
            _hr(),
            EMPLOYEE,
            skill.id,
            EmployeeSkillUpdate(proficiency=1),
            NOW,
        )


async def test_employee_skills_are_tenant_isolated(
    service: SkillService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    skill = await _create_skill(service)
    await service.record_employee_skill(
        _hr(),
        EMPLOYEE,
        EmployeeSkillCreate(skill_id=skill.id, proficiency=3),
        NOW,
    )
    tenant_b = _hr(TENANT_B)

    with pytest.raises(EmployeeNotFoundError):
        await service.list_employee_skills(tenant_b, EMPLOYEE)
    with pytest.raises(EmployeeNotFoundError):
        await service.record_employee_skill(
            tenant_b,
            EMPLOYEE,
            EmployeeSkillCreate(skill_id=skill.id, proficiency=1),
            NOW,
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.update_employee_skill(
            tenant_b,
            EMPLOYEE,
            skill.id,
            EmployeeSkillUpdate(proficiency=1),
            NOW,
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.remove_employee_skill(tenant_b, EMPLOYEE, skill.id, NOW)
