"""AC6 — tenant isolation on `/api/v1/users`: cross-tenant ids answer `404
user_not_found` (never `403`), and lists never leak rows across tenants."""

from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.identity.credentials import hash_password
from app.infrastructure.postgres.repositories.identity import PostgresUserRepository
from app.shared.identity import Role
from tests.integration.conftest import TENANT_A, TENANT_B, ClientFactory, identity

PASSWORD = "correct-horse-battery"


async def test_tenant_b_admin_cannot_touch_tenant_a_user(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as tenant_a_admin:
        created = (
            await tenant_a_admin.post(
                "/api/v1/users",
                json={
                    "email": "ada@workpulse.test",
                    "password": PASSWORD,
                    "role": "employee",
                },
            )
        ).json()

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        responses = [
            await tenant_b_admin.get(f"/api/v1/users/{created['id']}"),
            await tenant_b_admin.patch(
                f"/api/v1/users/{created['id']}",
                json={"email": "sneaky@workpulse.test"},
            ),
            await tenant_b_admin.post(f"/api/v1/users/{created['id']}/activate"),
            await tenant_b_admin.post(f"/api/v1/users/{created['id']}/deactivate"),
            await tenant_b_admin.post(
                f"/api/v1/users/{created['id']}/password",
                json={"new_password": "sneaky-new-password"},
            ),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json() == {
            "error": {"code": "user_not_found", "message": "User not found"}
        }

    # The denials changed nothing.
    async with client_factory() as tenant_a_admin:
        unchanged = (await tenant_a_admin.get(f"/api/v1/users/{created['id']}")).json()
    assert unchanged["email"] == "ada@workpulse.test"
    assert unchanged["role"] == "employee"
    assert unchanged["active"] is True


async def test_list_never_lists_across_tenants(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as tenant_a_admin:
        await tenant_a_admin.post(
            "/api/v1/users",
            json={
                "email": "a-only@workpulse.test",
                "password": PASSWORD,
                "role": "employee",
            },
        )

    await PostgresUserRepository(session).add(
        TENANT_B,
        uuid7(),
        email="b-only@workpulse.test",
        role=Role.ADMIN,
        employee_id=None,
        password_hash=await hash_password("irrelevant-password"),
    )

    async with client_factory(identity(tenant=TENANT_A)) as tenant_a_admin:
        listed_a = (await tenant_a_admin.get("/api/v1/users")).json()
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        listed_b = (await tenant_b_admin.get("/api/v1/users")).json()

    assert listed_a["page"]["total_items"] == 1
    assert [item["email"] for item in listed_a["items"]] == ["a-only@workpulse.test"]
    assert listed_b["page"]["total_items"] == 1
    assert [item["email"] for item in listed_b["items"]] == ["b-only@workpulse.test"]
    assert listed_a["items"][0]["tenant_id"] == str(TENANT_A)
    assert listed_b["items"][0]["tenant_id"] == str(TENANT_B)
