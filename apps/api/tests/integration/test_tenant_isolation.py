"""AC6 — tenant isolation: B never sees or mutates A's rows (404, not 403)."""

from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    identity_headers,
)


async def test_cross_tenant_department_mutations_all_404(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(identity_headers(tenant=TENANT_A)) as tenant_a:
        created = (
            await tenant_a.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()

    async with client_factory(identity_headers(tenant=TENANT_B)) as tenant_b:
        responses = [
            await tenant_b.get(f"/api/v1/departments/{created['id']}"),
            await tenant_b.patch(
                f"/api/v1/departments/{created['id']}", json={"name": "Hijacked"}
            ),
            await tenant_b.post(f"/api/v1/departments/{created['id']}/archive"),
            await tenant_b.post(f"/api/v1/departments/{created['id']}/restore"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "department_not_found"


async def test_cross_tenant_position_mutations_all_404(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(identity_headers(tenant=TENANT_A)) as tenant_a:
        created = (
            await tenant_a.post("/api/v1/positions", json={"title": "Senior Nurse"})
        ).json()

    async with client_factory(identity_headers(tenant=TENANT_B)) as tenant_b:
        responses = [
            await tenant_b.get(f"/api/v1/positions/{created['id']}"),
            await tenant_b.patch(
                f"/api/v1/positions/{created['id']}", json={"title": "X"}
            ),
            await tenant_b.post(f"/api/v1/positions/{created['id']}/archive"),
            await tenant_b.post(f"/api/v1/positions/{created['id']}/restore"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "position_not_found"


async def test_cross_tenant_location_mutations_all_404(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(identity_headers(tenant=TENANT_A)) as tenant_a:
        created = (
            await tenant_a.post(
                "/api/v1/locations",
                json={"name": "HQ", "timezone": "Asia/Ho_Chi_Minh"},
            )
        ).json()

    async with client_factory(identity_headers(tenant=TENANT_B)) as tenant_b:
        responses = [
            await tenant_b.get(f"/api/v1/locations/{created['id']}"),
            await tenant_b.patch(
                f"/api/v1/locations/{created['id']}", json={"name": "X"}
            ),
            await tenant_b.post(f"/api/v1/locations/{created['id']}/archive"),
            await tenant_b.post(f"/api/v1/locations/{created['id']}/restore"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "location_not_found"


async def test_lists_contain_no_foreign_rows(client_factory: ClientFactory) -> None:
    async with client_factory(identity_headers(tenant=TENANT_A)) as tenant_a:
        await tenant_a.post("/api/v1/departments", json={"name": "Engineering"})
        await tenant_a.post("/api/v1/positions", json={"title": "Senior Nurse"})
        await tenant_a.post(
            "/api/v1/locations", json={"name": "HQ", "timezone": "Asia/Ho_Chi_Minh"}
        )

    async with client_factory(identity_headers(tenant=TENANT_B)) as tenant_b:
        departments = (
            await tenant_b.get("/api/v1/departments", params={"include_archived": True})
        ).json()
        positions = (
            await tenant_b.get("/api/v1/positions", params={"include_archived": True})
        ).json()
        locations = (
            await tenant_b.get("/api/v1/locations", params={"include_archived": True})
        ).json()

    assert departments["items"] == [] and departments["page"]["total_items"] == 0
    assert positions["items"] == [] and positions["page"]["total_items"] == 0
    assert locations["items"] == [] and locations["page"]["total_items"] == 0


async def test_same_names_allowed_in_two_tenants(client_factory: ClientFactory) -> None:
    async with client_factory(identity_headers(tenant=TENANT_A)) as tenant_a:
        dept_a = await tenant_a.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )
        pos_a = await tenant_a.post("/api/v1/positions", json={"title": "Senior Nurse"})
        loc_a = await tenant_a.post(
            "/api/v1/locations", json={"name": "HQ", "timezone": "Asia/Ho_Chi_Minh"}
        )

    async with client_factory(identity_headers(tenant=TENANT_B)) as tenant_b:
        dept_b = await tenant_b.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )
        pos_b = await tenant_b.post("/api/v1/positions", json={"title": "Senior Nurse"})
        loc_b = await tenant_b.post(
            "/api/v1/locations", json={"name": "HQ", "timezone": "Asia/Ho_Chi_Minh"}
        )

    assert dept_a.status_code == dept_b.status_code == 201
    assert pos_a.status_code == pos_b.status_code == 201
    assert loc_a.status_code == loc_b.status_code == 201
    assert dept_a.json()["id"] != dept_b.json()["id"]
    assert dept_a.json()["tenant_id"] == str(TENANT_A)
    assert dept_b.json()["tenant_id"] == str(TENANT_B)
