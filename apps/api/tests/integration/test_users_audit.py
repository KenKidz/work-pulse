"""AC9 — every mutating users route writes exactly one audit entry: acting user
id, `entity_type: "user"`, and `changes` that never carry password material."""

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models_audit import AuditEntry
from app.shared.identity import Role
from tests.integration.conftest import TENANT_A, USER, ClientFactory, identity

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "stapled-battery-wrong"


async def _user_rows(session: AsyncSession) -> list[AuditEntry]:
    """Only identity entries — org/employee setup would be audited too."""
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "user")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def test_lifecycle_writes_one_row_per_mutation(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        created = (
            await admin.post(
                "/api/v1/users",
                json={
                    "email": "ada@workpulse.test",
                    "password": PASSWORD,
                    "role": "employee",
                },
            )
        ).json()
        await admin.patch(f"/api/v1/users/{created['id']}", json={"role": "manager"})
        await admin.post(f"/api/v1/users/{created['id']}/deactivate")
        # A redundant deactivate is a no-op — no second entry (archive idiom).
        await admin.post(f"/api/v1/users/{created['id']}/deactivate")
        await admin.post(f"/api/v1/users/{created['id']}/activate")
        await admin.post(
            f"/api/v1/users/{created['id']}/password",
            json={"new_password": NEW_PASSWORD},
        )

    rows = await _user_rows(session)
    assert [row.action for row in rows] == [
        "user.created",
        "user.updated",
        "user.deactivated",
        "user.activated",
        "user.password_reset",
    ]
    for row in rows:
        assert row.tenant_id == TENANT_A
        assert row.actor_id == USER  # the acting user id, never an employee id
        assert row.entity_type == "user"
        assert row.entity_id == UUID(created["id"])
        assert row.occurred_at is not None

    created_changes = rows[0].changes or {}
    assert created_changes["email"] == "changed"  # field name only, never the address
    assert created_changes["role"] == {"old": None, "new": "employee"}
    assert rows[1].changes == {"role": "changed"}

    # AC9: no password material in any `changes` payload.
    dumped = json.dumps([row.changes for row in rows])
    assert PASSWORD not in dumped
    assert NEW_PASSWORD not in dumped
    assert "argon2" not in dumped
    assert "ada@workpulse.test" not in dumped


async def test_failed_and_denied_mutations_write_no_rows(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        created = (
            await admin.post(
                "/api/v1/users",
                json={
                    "email": "ada@workpulse.test",
                    "password": PASSWORD,
                    "role": "employee",
                },
            )
        ).json()
        duplicate = await admin.post(
            "/api/v1/users",
            json={
                "email": "ada@workpulse.test",
                "password": PASSWORD,
                "role": "hr",
            },
        )
        assert duplicate.status_code == 409
        invalid = await admin.patch(
            f"/api/v1/users/{created['id']}", json={"email": None}
        )
        assert invalid.status_code == 422

    async with client_factory(identity(role=Role.HR)) as denied:
        assert (
            await denied.post(
                "/api/v1/users",
                json={
                    "email": "sneaky@workpulse.test",
                    "password": PASSWORD,
                    "role": "admin",
                },
            )
        ).status_code == 403
        assert (
            await denied.patch(f"/api/v1/users/{created['id']}", json={"role": "admin"})
        ).status_code == 403

    rows = await _user_rows(session)
    assert [row.action for row in rows] == ["user.created"]
