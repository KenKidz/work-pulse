"""AC8 — every real create/update/archive transition writes exactly one audit row."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models_audit import AuditEntry
from app.shared.identity import Role
from tests.integration.conftest import TENANT_A, USER, ClientFactory, identity


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(select(AuditEntry))
    return list(result.scalars().all())


async def test_department_lifecycle_writes_one_row_per_transition(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()
        await client.patch(f"/api/v1/departments/{created['id']}", json={"name": "R&D"})
        await client.post(
            f"/api/v1/departments/{created['id']}/archive", json={"reason": "reorg"}
        )
        await client.post(f"/api/v1/departments/{created['id']}/restore")

    rows = await _audit_rows(session)
    assert len(rows) == 4
    assert [row.action for row in rows] == [
        "department.created",
        "department.updated",
        "department.archived",
        "department.restored",
    ]
    for row in rows:
        assert row.tenant_id == TENANT_A
        assert row.actor_id == USER  # the acting user id
        assert str(row.entity_id) == created["id"]
        assert row.entity_type == "department"
        assert row.occurred_at is not None

    archived_row = rows[2]
    assert archived_row is not None
    assert archived_row.reason == "reorg"  # reason lives on the audit entry only


async def test_update_changes_hold_old_and_new_name(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()
        await client.patch(f"/api/v1/departments/{created['id']}", json={"name": "R&D"})

    rows = await _audit_rows(session)
    updated = rows[1]
    assert updated is not None
    assert updated.changes == {"name": {"old": "Engineering", "new": "R&D"}}


async def test_noop_archive_and_restore_write_no_rows(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()
        # restore-of-active is a no-op
        first_restore = await client.post(
            f"/api/v1/departments/{created['id']}/restore"
        )
        assert first_restore.status_code == 200

        await client.post(f"/api/v1/departments/{created['id']}/archive")
        # archive-of-archived is a no-op
        second_archive = await client.post(
            f"/api/v1/departments/{created['id']}/archive"
        )
        assert second_archive.status_code == 200

    rows = await _audit_rows(session)
    assert [row.action for row in rows] == [
        "department.created",
        "department.archived",
    ]


async def test_position_and_location_mutations_are_audited(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        position = (
            await client.post("/api/v1/positions", json={"title": "Senior Nurse"})
        ).json()
        await client.patch(
            f"/api/v1/positions/{position['id']}", json={"title": "Nurse Lead"}
        )
        location = (
            await client.post(
                "/api/v1/locations", json={"name": "HQ", "timezone": "Asia/Ho_Chi_Minh"}
            )
        ).json()
        await client.post(f"/api/v1/locations/{location['id']}/archive")

    rows = await _audit_rows(session)
    assert [(row.entity_type, row.action) for row in rows] == [
        ("position", "position.created"),
        ("position", "position.updated"),
        ("location", "location.created"),
        ("location", "location.archived"),
    ]


async def test_failed_mutations_write_no_audit_rows(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        await client.post("/api/v1/departments", json={"name": "Engineering"})
        duplicate = await client.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )
        assert duplicate.status_code == 409

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        denied = await employee.post("/api/v1/departments", json={"name": "Nope"})
        assert denied.status_code == 403

    rows = await _audit_rows(session)
    assert [row.action for row in rows] == ["department.created"]
