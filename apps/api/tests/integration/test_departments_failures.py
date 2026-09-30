"""Failure-path HTTP tests for /api/v1/departments — AC3, AC4, 404s."""

from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    identity,
)

UNKNOWN_ID = "00000000-0000-4000-8000-00000000dead"


async def test_duplicate_name_returns_409(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        first = await client.post("/api/v1/departments", json={"name": "Engineering"})
        duplicate = await client.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409  # AC3
    assert duplicate.json() == {
        "error": {
            "code": "department_name_taken",
            "message": "Department name already taken in this tenant",
        }
    }


async def test_archived_row_keeps_its_name(client_factory: ClientFactory) -> None:
    """Uniqueness spans archived rows: the name stays taken (pinned contract)."""
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Legacy"})
        ).json()
        await client.post(f"/api/v1/departments/{created['id']}/archive")

        recreate = await client.post("/api/v1/departments", json={"name": "Legacy"})

    assert recreate.status_code == 409
    assert recreate.json()["error"]["code"] == "department_name_taken"


async def test_self_parent_returns_400_cycle(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()

        response = await client.patch(
            f"/api/v1/departments/{created['id']}",
            json={"parent_department_id": created["id"]},
        )

    assert response.status_code == 400  # AC4
    assert response.json()["error"]["code"] == "department_cycle"


async def test_descendant_parent_returns_400_cycle(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        root = (
            await client.post("/api/v1/departments", json={"name": "Company"})
        ).json()
        child = (
            await client.post(
                "/api/v1/departments",
                json={"name": "Engineering", "parent_department_id": root["id"]},
            )
        ).json()
        grandchild = (
            await client.post(
                "/api/v1/departments",
                json={"name": "Backend", "parent_department_id": child["id"]},
            )
        ).json()

        response = await client.patch(
            f"/api/v1/departments/{root['id']}",
            json={"parent_department_id": grandchild["id"]},
        )

    assert response.status_code == 400  # AC4
    assert response.json()["error"]["code"] == "department_cycle"


async def test_unknown_id_returns_404(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        get = await client.get(f"/api/v1/departments/{UNKNOWN_ID}")
        patch = await client.patch(
            f"/api/v1/departments/{UNKNOWN_ID}", json={"name": "X"}
        )
        archive = await client.post(f"/api/v1/departments/{UNKNOWN_ID}/archive")
        restore = await client.post(f"/api/v1/departments/{UNKNOWN_ID}/restore")

    for response in (get, patch, archive, restore):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "department_not_found"


async def test_parent_not_in_tenant_returns_404(client_factory: ClientFactory) -> None:
    """Parent must exist IN THE TENANT — a foreign id is a 404, never a 403."""
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        foreign = (
            await tenant_b.post("/api/v1/departments", json={"name": "Foreign"})
        ).json()

    async with client_factory(identity(tenant=TENANT_A)) as tenant_a:
        create = await tenant_a.post(
            "/api/v1/departments",
            json={"name": "Sneaky", "parent_department_id": foreign["id"]},
        )
        own = (await tenant_a.post("/api/v1/departments", json={"name": "Own"})).json()
        patch = await tenant_a.patch(
            f"/api/v1/departments/{own['id']}",
            json={"parent_department_id": foreign["id"]},
        )

    assert create.status_code == 404
    assert create.json()["error"]["code"] == "department_not_found"
    assert patch.status_code == 404
    assert patch.json()["error"]["code"] == "department_not_found"


async def test_explicit_null_name_is_validation_error(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()

        response = await client.patch(
            f"/api/v1/departments/{created['id']}", json={"name": None}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_malformed_id_is_validation_error(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        response = await client.get("/api/v1/departments/not-a-uuid")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "not-a-uuid" not in body["error"]["message"]  # never echo field values


async def test_tenant_b_sees_no_error_detail_from_tenant_a(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(identity(tenant=TENANT_A)) as tenant_a:
        created = (
            await tenant_a.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        response = await tenant_b.get(f"/api/v1/departments/{created['id']}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "department_not_found"
