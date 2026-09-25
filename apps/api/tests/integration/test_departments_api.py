"""HTTP integration tests for /api/v1/departments — AC1, AC2 and the pinned contract."""

from datetime import UTC, datetime

from app.domains.organizations.schemas import DepartmentCreate
from app.domains.organizations.service import DepartmentService
from app.shared.identity import Role
from tests.integration.conftest import TENANT_A, ClientFactory, dev_identity

PINNED_DEPARTMENT_KEYS = {
    "id",
    "tenant_id",
    "name",
    "parent_department_id",
    "archived_at",
    "created_at",
    "updated_at",
}


async def test_create_returns_201_with_exact_body_shape(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == PINNED_DEPARTMENT_KEYS  # AC1: exact pinned shape
    assert body["name"] == "Engineering"
    assert body["tenant_id"] == str(TENANT_A)
    assert body["parent_department_id"] is None
    assert body["archived_at"] is None
    assert isinstance(body["created_at"], str) and isinstance(body["updated_at"], str)
    assert isinstance(body["id"], str) and len(body["id"]) == 36


async def test_list_paginates_and_filters(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        root = (
            await client.post("/api/v1/departments", json={"name": "Company"})
        ).json()
        await client.post(
            "/api/v1/departments",
            json={"name": "Backend", "parent_department_id": root["id"]},
        )
        await client.post(
            "/api/v1/departments",
            json={"name": "Frontend", "parent_department_id": root["id"]},
        )
        await client.post("/api/v1/departments", json={"name": "People Ops"})

        default = (await client.get("/api/v1/departments")).json()
        assert default["page"] == {
            "page": 1,
            "page_size": 20,
            "total_items": 4,
            "total_pages": 1,
        }
        assert [item["name"] for item in default["items"]] == [
            "Backend",
            "Company",
            "Frontend",
            "People Ops",
        ]  # name ascending

        # AC2: q — case-insensitive substring
        by_q = (await client.get("/api/v1/departments", params={"q": "end"})).json()
        assert [item["name"] for item in by_q["items"]] == ["Backend", "Frontend"]

        # AC2: parent_id — exact UUID
        by_parent = (
            await client.get("/api/v1/departments", params={"parent_id": root["id"]})
        ).json()
        assert by_parent["page"]["total_items"] == 2

        # AC2: pagination across pages
        page1 = (
            await client.get("/api/v1/departments", params={"page": 1, "page_size": 3})
        ).json()
        page2 = (
            await client.get("/api/v1/departments", params={"page": 2, "page_size": 3})
        ).json()
        assert len(page1["items"]) == 3
        assert len(page2["items"]) == 1
        assert page2["page"]["total_pages"] == 2

        # page_size above the max is clamped, never an error
        clamped = (
            await client.get("/api/v1/departments", params={"page_size": 1000})
        ).json()
        assert clamped["page"]["page_size"] == 100

        # page below 1 is clamped too
        page0 = (await client.get("/api/v1/departments", params={"page": 0})).json()
        assert page0["page"]["page"] == 1


async def test_list_pagination_over_25_rows(
    client_factory: ClientFactory, department_service: DepartmentService
) -> None:
    identity = dev_identity(role=Role.ADMIN)
    for index in range(25):
        await department_service.create(
            identity,
            DepartmentCreate(name=f"dept-{index:02d}"),
            datetime(2026, 9, 24, tzinfo=UTC),
        )

    async with client_factory() as client:
        page1 = (await client.get("/api/v1/departments")).json()
        page2 = (await client.get("/api/v1/departments", params={"page": 2})).json()

    assert page1["page"] == {
        "page": 1,
        "page_size": 20,
        "total_items": 25,
        "total_pages": 2,
    }
    assert len(page1["items"]) == 20
    assert len(page2["items"]) == 5


async def test_get_patch_rename_reparent_and_null_to_root(
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

        fetched = await client.get(f"/api/v1/departments/{child['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Engineering"

        renamed = await client.patch(
            f"/api/v1/departments/{child['id']}", json={"name": "R&D"}
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "R&D"
        assert renamed.json()["parent_department_id"] == root["id"]  # untouched

        moved = await client.patch(
            f"/api/v1/departments/{child['id']}", json={"parent_department_id": None}
        )
        assert moved.status_code == 200
        assert moved.json()["parent_department_id"] is None  # explicit null → root

        empty_patch = await client.patch(f"/api/v1/departments/{child['id']}", json={})
        assert empty_patch.status_code == 200
        assert empty_patch.json()["name"] == "R&D"


async def test_archive_hides_from_list_but_stays_readable_then_restores(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Legacy"})
        ).json()

        archived = await client.post(
            f"/api/v1/departments/{created['id']}/archive", json={"reason": "reorg"}
        )
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        default_list = (await client.get("/api/v1/departments")).json()
        assert default_list["page"]["total_items"] == 0  # hidden by default

        with_archived = (
            await client.get("/api/v1/departments", params={"include_archived": True})
        ).json()
        assert with_archived["page"]["total_items"] == 1

        still_readable = await client.get(f"/api/v1/departments/{created['id']}")
        assert still_readable.status_code == 200
        assert still_readable.json()["archived_at"] is not None

        # archive-of-archived is a 200 no-op
        noop_archive = await client.post(f"/api/v1/departments/{created['id']}/archive")
        assert noop_archive.status_code == 200

        restored = await client.post(f"/api/v1/departments/{created['id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None

        # restore-of-active is a 200 no-op
        noop_restore = await client.post(f"/api/v1/departments/{created['id']}/restore")
        assert noop_restore.status_code == 200
        assert noop_restore.json()["archived_at"] is None


async def test_archived_department_can_still_be_patched(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        created = (
            await client.post("/api/v1/departments", json={"name": "Legacy"})
        ).json()
        await client.post(f"/api/v1/departments/{created['id']}/archive")

        patched = await client.patch(
            f"/api/v1/departments/{created['id']}", json={"name": "Legacy Renamed"}
        )

    assert patched.status_code == 200
    assert patched.json()["name"] == "Legacy Renamed"
    assert patched.json()["archived_at"] is not None
