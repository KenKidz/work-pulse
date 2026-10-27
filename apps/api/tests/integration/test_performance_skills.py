"""HTTP integration tests for the performance skill catalog and employee skills
(T7).

Every test drives the real router, `SkillService` and PostgreSQL adapter through
the shared session. The router clock is injected (`performance_api._now`), so
timestamps never depend on wall time.

Covered here (`.ai/features/performance.md` → T7, acceptance criteria 6–8):
catalog create/rename/archive/restore with no-op repeats, name uniqueness across
archived rows (`409 skill_name_taken`), attaching an archived skill (`409
skill_archived`), duplicate attach (`409 employee_skill_exists`), `q` +
`include_archived` listings, employee-skill update/remove, role denials
(Employee catalog `403`, Finance record `403`), foreign ids as `404`, tenant
isolation, and exactly one audit entry per mutation.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import performance as performance_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MANAGER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000021")
REPORT_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000022")
PEER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000023")
UNKNOWN_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000ee")
UNKNOWN_SKILL = UUID("00000000-0000-4000-8000-0000000000ff")

PII_SKILL_DESCRIPTION = "Internal calibration notes — pii-skill-description"
PII_NOTE = "Confidential proficiency note — pii-employee-skill-note"

PINNED_SKILL_KEYS = {"id", "name", "description", "archived_at"}
PINNED_EMPLOYEE_SKILL_KEYS = {
    "skill_id",
    "skill_name",
    "proficiency",
    "note",
    "created_at",
    "updated_at",
}


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


def _hr() -> Any:
    return identity(role=Role.HR, employee_id=None)


def _employee() -> Any:
    return identity(role=Role.EMPLOYEE, employee_id=ACTOR)


def _manager() -> Any:
    return identity(role=Role.MANAGER, employee_id=MANAGER_EMPLOYEE)


def _finance() -> Any:
    return identity(role=Role.FINANCE, employee_id=ACTOR)


async def _seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    employee_id: UUID = ACTOR,
    code: str = "EMP-00009",
    email: str = "self@example.test",
    first_name: str = "Actor",
    last_name: str = "Self",
    manager_id: UUID | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
    tenant_id: UUID = TENANT_A,
) -> None:
    await PostgresEmployeeRepository(session).add(
        tenant_id,
        employee_id,
        employee_code=code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=status,
        hired_at=date(2026, 1, 5),
    )


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)
    await _seed_employee(
        session,
        refs,
        employee_id=MANAGER_EMPLOYEE,
        code="EMP-00021",
        email="manager@example.test",
        first_name="Morgan",
        last_name="Manager",
    )
    await _seed_employee(
        session,
        refs,
        employee_id=REPORT_EMPLOYEE,
        code="EMP-00022",
        email="report@example.test",
        first_name="Riley",
        last_name="Report",
        manager_id=MANAGER_EMPLOYEE,
    )
    await _seed_employee(
        session,
        refs,
        employee_id=PEER_EMPLOYEE,
        code="EMP-00023",
        email="peer@example.test",
        first_name="Parker",
        last_name="Peer",
    )


async def _create_skill(
    client: AsyncClient,
    *,
    name: str = "Python",
    description: str | None = None,
) -> Response:
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    return await client.post("/api/v1/performance/skills", json=body)


async def _record_skill(
    client: AsyncClient,
    employee_id: UUID,
    *,
    skill_id: str,
    proficiency: int = 3,
    note: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "skill_id": skill_id,
        "proficiency": proficiency,
    }
    if note is not None:
        body["note"] = note
    return await client.post(
        f"/api/v1/performance/employees/{employee_id}/skills", json=body
    )


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type.in_(("skill", "employee_skill")))
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def test_catalog_and_employee_skill_hop(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_hr()) as hr:
        created = await _create_skill(
            hr, name="Python", description=PII_SKILL_DESCRIPTION
        )
        skill = created.json()
        skill_id = skill["id"]

        renamed = await hr.patch(
            f"/api/v1/performance/skills/{skill_id}",
            json={"name": "Python 3", "description": PII_SKILL_DESCRIPTION},
        )
        archived = await hr.post(f"/api/v1/performance/skills/{skill_id}/archive")
        # Uniqueness spans archived rows: the same name is still taken.
        duplicate = await _create_skill(hr, name="Python 3")
        archived_attach = await _record_skill(
            hr, ACTOR, skill_id=skill_id, proficiency=4
        )
        restored = await hr.post(f"/api/v1/performance/skills/{skill_id}/restore")

        recorded = await _record_skill(
            hr,
            ACTOR,
            skill_id=skill_id,
            proficiency=4,
            note=PII_NOTE,
        )
        duplicate_attach = await _record_skill(
            hr, ACTOR, skill_id=skill_id, proficiency=2
        )
        unknown_skill_attach = await _record_skill(
            hr, ACTOR, skill_id=str(UNKNOWN_SKILL)
        )
        unknown_employee_attach = await _record_skill(
            hr, UNKNOWN_EMPLOYEE, skill_id=skill_id
        )

    assert created.status_code == 201
    assert set(skill) == PINNED_SKILL_KEYS
    assert skill["name"] == "Python"
    assert skill["description"] == PII_SKILL_DESCRIPTION
    assert skill["archived_at"] is None

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Python 3"

    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    _assert_error(duplicate, 409, "skill_name_taken")
    _assert_error(archived_attach, 409, "skill_archived")

    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None

    assert recorded.status_code == 201
    recorded_body = recorded.json()
    assert set(recorded_body) == PINNED_EMPLOYEE_SKILL_KEYS
    assert recorded_body["skill_id"] == skill_id
    assert recorded_body["skill_name"] == "Python 3"
    assert recorded_body["proficiency"] == 4
    assert recorded_body["note"] == PII_NOTE
    _assert_error(duplicate_attach, 409, "employee_skill_exists")
    _assert_error(unknown_skill_attach, 404, "skill_not_found")
    _assert_error(unknown_employee_attach, 404, "employee_not_found")

    async with client_factory(_employee()) as employee:
        listed = await employee.get(f"/api/v1/performance/employees/{ACTOR}/skills")
        updated = await employee.patch(
            f"/api/v1/performance/employees/{ACTOR}/skills/{skill_id}",
            json={"proficiency": 5},
        )
        feedback_peek = await employee.get(
            f"/api/v1/performance/employees/{PEER_EMPLOYEE}/skills"
        )
        removed = await employee.delete(
            f"/api/v1/performance/employees/{ACTOR}/skills/{skill_id}"
        )
        removed_again = await employee.delete(
            f"/api/v1/performance/employees/{ACTOR}/skills/{skill_id}"
        )
        catalog_search = await employee.get(
            "/api/v1/performance/skills", params={"q": "PYTHON"}
        )
        catalog_archived = await employee.get(
            "/api/v1/performance/skills",
            params={"q": "PYTHON", "include_archived": "true"},
        )

    assert listed.status_code == 200
    assert set(listed.json()) == {"items"}
    assert [row["skill_id"] for row in listed.json()["items"]] == [skill_id]
    assert updated.status_code == 200
    assert updated.json()["proficiency"] == 5
    _assert_error(feedback_peek, 404, "employee_not_found")
    assert removed.status_code == 204
    assert removed.content == b""
    _assert_error(removed_again, 404, "skill_not_found")

    assert catalog_search.status_code == 200
    assert [row["name"] for row in catalog_search.json()["items"]] == ["Python 3"]
    assert catalog_archived.status_code == 200
    assert [row["name"] for row in catalog_archived.json()["items"]] == ["Python 3"]

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "skill.created",
        "skill.updated",
        "skill.archived",
        "skill.restored",
        "employee_skill.recorded",
        "employee_skill.updated",
        "employee_skill.removed",
    ]
    assert {row.actor_id for row in audit} == {USER}
    assert audit[0].changes is not None
    assert audit[0].changes["description"] == "changed"
    assert audit[4].changes is not None
    assert audit[4].changes["note"] == "changed"
    encoded = str([row.changes for row in audit])
    assert PII_SKILL_DESCRIPTION not in encoded
    assert PII_NOTE not in encoded


async def test_skill_roles_and_tenant_isolation(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory(_hr()) as hr:
        skill_id = (await _create_skill(hr)).json()["id"]
        second_skill_id = (await _create_skill(hr, name="Go")).json()["id"]
        await _record_skill(hr, REPORT_EMPLOYEE, skill_id=skill_id)

    async with client_factory(_manager()) as manager:
        report_skills = await manager.get(
            f"/api/v1/performance/employees/{REPORT_EMPLOYEE}/skills"
        )
        peer_skills = await manager.get(
            f"/api/v1/performance/employees/{PEER_EMPLOYEE}/skills"
        )
        can_record_report = await _record_skill(
            manager, MANAGER_EMPLOYEE, skill_id=skill_id, proficiency=2
        )

    assert report_skills.status_code == 200
    assert len(report_skills.json()["items"]) == 1
    _assert_error(peer_skills, 404, "employee_not_found")
    assert can_record_report.status_code == 201

    async with client_factory(_employee()) as employee:
        # AC 6: the employee themself attaches an active catalog skill.
        self_attach = await _record_skill(
            employee, ACTOR, skill_id=second_skill_id, proficiency=3
        )
        forbidden_create = await _create_skill(employee, name="Rust")
        forbidden_update = await employee.patch(
            f"/api/v1/performance/skills/{skill_id}", json={"name": "Rust"}
        )
        forbidden_archive = await employee.post(
            f"/api/v1/performance/skills/{skill_id}/archive"
        )
        forbidden_restore = await employee.post(
            f"/api/v1/performance/skills/{skill_id}/restore"
        )

    assert self_attach.status_code == 201
    assert self_attach.json()["skill_name"] == "Go"

    for response in (
        forbidden_create,
        forbidden_update,
        forbidden_archive,
        forbidden_restore,
    ):
        _assert_error(response, 403, "performance_denied")

    async with client_factory(_finance()) as finance:
        forbidden_record = await _record_skill(
            finance, ACTOR, skill_id=skill_id, proficiency=1
        )

    _assert_error(forbidden_record, 403, "performance_denied")

    tenant_b = identity(
        tenant=TENANT_B,
        role=Role.HR,
        user_id=UUID("00000000-0000-4000-8000-0000000000c1"),
        employee_id=None,
    )
    async with client_factory(tenant_b) as other_tenant:
        tenant_b_list = await other_tenant.get("/api/v1/performance/skills")
        cross_update = await other_tenant.patch(
            f"/api/v1/performance/skills/{skill_id}", json={"name": "Stolen"}
        )
        cross_attach = await _record_skill(
            other_tenant, ACTOR, skill_id=skill_id, proficiency=1
        )
        cross_list = await other_tenant.get(
            f"/api/v1/performance/employees/{ACTOR}/skills"
        )

    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json()["items"] == []
    _assert_error(cross_update, 404, "skill_not_found")
    _assert_error(cross_attach, 404, "employee_not_found")
    _assert_error(cross_list, 404, "employee_not_found")
