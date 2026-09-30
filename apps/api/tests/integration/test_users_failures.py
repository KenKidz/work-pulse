"""Failure-path HTTP tests for `/api/v1/users` — AC7 conflicts + validation.

Pinned error codes (`.ai/features/identity-access.md` → Error code → status):
`user_email_taken` / `employee_link_taken` / `last_admin` 409,
`employee_not_found` 404 (cross-tenant links answer like missing ones),
`validation_error` 422 (short password, explicit null email/role in PATCH).
"""

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.identity.credentials import hash_password
from app.infrastructure.postgres.repositories.identity import PostgresUserRepository
from app.shared.identity import Role
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)

PASSWORD = "correct-horse-battery"


async def _create_user(
    client: AsyncClient,
    *,
    email: str = "ada@workpulse.test",
    password: str = PASSWORD,
    role: str = "employee",
    employee_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"email": email, "password": password, "role": role}
    if employee_id is not None:
        payload["employee_id"] = employee_id
    return (await client.post("/api/v1/users", json=payload)).json()


async def _seed_user(session: AsyncSession, email: str, tenant: UUID = TENANT_A):
    """One user straight through the repository (pre-existing account)."""
    return await PostgresUserRepository(session).add(
        tenant,
        uuid7(),
        email=email,
        role=Role.ADMIN,
        employee_id=None,
        password_hash=await hash_password("irrelevant-password"),
    )


async def test_duplicate_email_is_409(client_factory: ClientFactory) -> None:
    async with client_factory() as admin:
        first = await admin.post(
            "/api/v1/users",
            json={
                "email": "ada@workpulse.test",
                "password": PASSWORD,
                "role": "employee",
            },
        )
        assert first.status_code == 201

        duplicate = await admin.post(
            "/api/v1/users",
            json={"email": "ada@workpulse.test", "password": PASSWORD, "role": "hr"},
        )

    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "error": {"code": "user_email_taken", "message": "User email already taken"}
    }


async def test_email_held_by_another_tenant_is_still_409(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """Decision 7: email uniqueness is global — the account resolves the tenant."""
    await _seed_user(session, "shared@workpulse.test", tenant=TENANT_B)

    async with client_factory() as tenant_a_admin:
        response = await tenant_a_admin.post(
            "/api/v1/users",
            json={
                "email": "shared@workpulse.test",
                "password": PASSWORD,
                "role": "employee",
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "user_email_taken"


async def test_already_linked_employee_is_409(client_factory: ClientFactory) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        employee = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        linked = await _create_user(admin, employee_id=employee["id"])
        assert linked["employee_id"] == employee["id"]

        second = await admin.post(
            "/api/v1/users",
            json={
                "email": "second@workpulse.test",
                "password": PASSWORD,
                "role": "employee",
                "employee_id": employee["id"],
            },
        )

    assert second.status_code == 409
    assert second.json() == {
        "error": {
            "code": "employee_link_taken",
            "message": "Employee is already linked to a user",
        }
    }


async def test_other_tenants_employee_is_404(
    client_factory: ClientFactory,
) -> None:
    """Cross-tenant link → `404 employee_not_found` on create and PATCH."""
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        refs_b = await create_org_refs(tenant_b)
        foreign_employee = (
            await tenant_b.post("/api/v1/employees", json=employee_payload(refs_b))
        ).json()

    async with client_factory() as tenant_a_admin:
        created = await tenant_a_admin.post(
            "/api/v1/users",
            json={
                "email": "ada@workpulse.test",
                "password": PASSWORD,
                "role": "employee",
                "employee_id": foreign_employee["id"],
            },
        )
        existing = await _create_user(tenant_a_admin, email="bo@workpulse.test")
        patched = await tenant_a_admin.patch(
            f"/api/v1/users/{existing['id']}",
            json={"employee_id": foreign_employee["id"]},
        )

    assert created.status_code == 404
    assert created.json()["error"]["code"] == "employee_not_found"
    assert patched.status_code == 404
    assert patched.json()["error"]["code"] == "employee_not_found"


async def test_deactivating_last_admin_is_409(client_factory: ClientFactory) -> None:
    async with client_factory() as admin:
        first = await _create_user(
            admin, email="admin-one@workpulse.test", role="admin"
        )
        denied = await admin.post(f"/api/v1/users/{first['id']}/deactivate")
        assert denied.status_code == 409
        assert denied.json() == {
            "error": {
                "code": "last_admin",
                "message": "Cannot deactivate the last active admin",
            }
        }

        # A second admin changes the count — and super_admin counts too.
        super_admin = await _create_user(
            admin, email="root@workpulse.test", role="super_admin"
        )
        assert (
            await admin.post(f"/api/v1/users/{first['id']}/deactivate")
        ).status_code == 200
        # Now the super_admin is the last one standing.
        last = await admin.post(f"/api/v1/users/{super_admin['id']}/deactivate")
        assert last.status_code == 409
        assert last.json()["error"]["code"] == "last_admin"


async def test_patch_explicit_null_required_fields_is_422(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        created = await _create_user(admin)
        for body in ({"email": None}, {"role": None}):
            response = await admin.patch(f"/api/v1/users/{created['id']}", json=body)
            assert response.status_code == 422
            assert response.json() == {
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                }
            }


async def test_short_passwords_are_validation_errors(
    client_factory: ClientFactory,
) -> None:
    """Pinned password rule: minimum 10 characters → 422 below it."""
    async with client_factory() as admin:
        created = await admin.post(
            "/api/v1/users",
            json={"email": "ada@workpulse.test", "password": "ninechars", "role": "hr"},
        )
        assert created.status_code == 422
        assert created.json()["error"]["code"] == "validation_error"

        user = await _create_user(admin)
        reset = await admin.post(
            f"/api/v1/users/{user['id']}/password", json={"new_password": "ninechars"}
        )
        assert reset.status_code == 422
        assert reset.json()["error"]["code"] == "validation_error"
