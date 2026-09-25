"""HTTP tests for /api/v1/locations — CRUD, name_taken, invalid_timezone."""

from tests.integration.conftest import ClientFactory

PINNED_LOCATION_KEYS = {
    "id",
    "tenant_id",
    "name",
    "address",
    "timezone",
    "archived_at",
    "created_at",
    "updated_at",
}


async def test_location_crud_happy_path(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        created = await client.post(
            "/api/v1/locations",
            json={"name": "HQ", "address": "1 Main St", "timezone": "Asia/Ho_Chi_Minh"},
        )
        assert created.status_code == 201
        body = created.json()
        assert set(body) == PINNED_LOCATION_KEYS
        assert body["timezone"] == "Asia/Ho_Chi_Minh"
        assert body["address"] == "1 Main St"

        fetched = await client.get(f"/api/v1/locations/{body['id']}")
        assert fetched.status_code == 200

        listed = (await client.get("/api/v1/locations", params={"q": "hq"})).json()
        assert listed["page"]["total_items"] == 1

        patched = await client.patch(
            f"/api/v1/locations/{body['id']}", json={"timezone": "Europe/Berlin"}
        )
        assert patched.status_code == 200
        assert patched.json()["timezone"] == "Europe/Berlin"

        archived = await client.post(
            f"/api/v1/locations/{body['id']}/archive", json={"reason": "office closed"}
        )
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        restored = await client.post(f"/api/v1/locations/{body['id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None


async def test_duplicate_location_name_returns_409(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        first = await client.post(
            "/api/v1/locations", json={"name": "HQ", "timezone": "UTC"}
        )
        duplicate = await client.post(
            "/api/v1/locations", json={"name": "HQ", "timezone": "UTC"}
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "location_name_taken"


async def test_invalid_timezone_on_create_returns_422(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/locations",
            json={"name": "Mars Base", "timezone": "Mars/Olympus_Mons"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_timezone"
    assert set(body) == {"error"}  # uniform shape, never FastAPI's detail
    assert "Mars/Olympus_Mons" not in body["error"]["message"]


async def test_invalid_timezone_on_patch_returns_422(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        created = (
            await client.post(
                "/api/v1/locations", json={"name": "HQ", "timezone": "Asia/Ho_Chi_Minh"}
            )
        ).json()

        response = await client.patch(
            f"/api/v1/locations/{created['id']}", json={"timezone": "Not/AZone"}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_timezone"
