"""HTTP integration tests for /api/v1/positions — CRUD happy path + title_taken."""

from tests.integration.conftest import ClientFactory

PINNED_POSITION_KEYS = {
    "id",
    "tenant_id",
    "title",
    "description",
    "archived_at",
    "created_at",
    "updated_at",
}


async def test_position_crud_happy_path(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        created = await client.post(
            "/api/v1/positions",
            json={"title": "Senior Nurse", "description": "Ward lead"},
        )
        assert created.status_code == 201
        body = created.json()
        assert set(body) == PINNED_POSITION_KEYS
        assert body["title"] == "Senior Nurse"
        assert body["description"] == "Ward lead"
        assert body["archived_at"] is None

        fetched = await client.get(f"/api/v1/positions/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["title"] == "Senior Nurse"

        listed = (await client.get("/api/v1/positions", params={"q": "nurse"})).json()
        assert listed["page"]["total_items"] == 1
        assert listed["items"][0]["id"] == body["id"]

        patched = await client.patch(
            f"/api/v1/positions/{body['id']}", json={"description": None}
        )
        assert patched.status_code == 200
        assert patched.json()["description"] is None  # explicit null clears
        assert patched.json()["title"] == "Senior Nurse"  # absent field unchanged

        archived = await client.post(f"/api/v1/positions/{body['id']}/archive")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        hidden = (await client.get("/api/v1/positions")).json()
        assert hidden["page"]["total_items"] == 0

        restored = await client.post(f"/api/v1/positions/{body['id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None


async def test_duplicate_position_title_returns_409(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        first = await client.post("/api/v1/positions", json={"title": "Senior Nurse"})
        duplicate = await client.post(
            "/api/v1/positions", json={"title": "Senior Nurse"}
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "position_title_taken"


async def test_position_rename_to_duplicate_returns_409(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        await client.post("/api/v1/positions", json={"title": "Senior Nurse"})
        other = (
            await client.post("/api/v1/positions", json={"title": "Junior Nurse"})
        ).json()

        response = await client.patch(
            f"/api/v1/positions/{other['id']}", json={"title": "Senior Nurse"}
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "position_title_taken"
