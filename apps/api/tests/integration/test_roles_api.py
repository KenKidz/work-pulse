"""AC7 — role enforcement at the HTTP edge, fail-closed identity handling."""

import pytest

from tests.integration.conftest import ClientFactory, identity_headers

READ_PATHS = ["/api/v1/departments", "/api/v1/positions", "/api/v1/locations"]


@pytest.mark.parametrize("role", ["employee", "manager", "finance"])
async def test_non_manage_roles_cannot_create(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity_headers(role=role)) as client:
        department = await client.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )
        position = await client.post("/api/v1/positions", json={"title": "Nurse"})
        location = await client.post(
            "/api/v1/locations", json={"name": "HQ", "timezone": "UTC"}
        )

    for response in (department, position, location):
        assert response.status_code == 403  # AC7
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", ["employee", "manager", "finance"])
async def test_non_manage_roles_cannot_archive(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity_headers(role="admin")) as admin:
        created = (
            await admin.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()

    async with client_factory(identity_headers(role=role)) as client:
        response = await client.post(f"/api/v1/departments/{created['id']}/archive")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(
    "role", ["employee", "manager", "hr", "finance", "admin", "super_admin"]
)
async def test_every_valid_role_can_read(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity_headers(role=role)) as client:
        for path in READ_PATHS:
            response = await client.get(path)
            assert response.status_code == 200, f"{role} GET {path}"


@pytest.mark.parametrize("role", ["hr", "admin", "super_admin"])
async def test_manage_roles_can_create(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity_headers(role=role)) as client:
        response = await client.post(
            "/api/v1/departments", json={"name": f"Dept {role}"}
        )

    assert response.status_code == 201


async def test_hr_create_and_admin_archive(client_factory: ClientFactory) -> None:
    async with client_factory(identity_headers(role="hr")) as hr:
        created = await hr.post("/api/v1/departments", json={"name": "Engineering"})
    assert created.status_code == 201

    async with client_factory(identity_headers(role="admin")) as admin:
        archived = await admin.post(
            f"/api/v1/departments/{created.json()['id']}/archive",
            json={"reason": "reorg"},
        )
    assert archived.status_code == 200


async def test_missing_headers_return_400_invalid_identity(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(headers={}) as client:
        responses = [
            await client.get("/api/v1/departments"),
            await client.post("/api/v1/departments", json={"name": "Engineering"}),
        ]

    for response in responses:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_identity"


@pytest.mark.parametrize(
    "headers",
    [
        {
            "X-Actor-Id": "00000000-0000-4000-8000-000000000002",
            "X-Role": "admin",
        },  # no tenant
        {
            "X-Tenant-Id": "00000000-0000-4000-8000-00000000000a",
            "X-Role": "admin",
        },  # no actor
        {
            "X-Tenant-Id": "00000000-0000-4000-8000-00000000000a",
            "X-Actor-Id": "00000000-0000-4000-8000-000000000002",
        },  # no role
        {**identity_headers(), "X-Tenant-Id": "not-a-uuid"},
        {**identity_headers(), "X-Actor-Id": "not-a-uuid"},
    ],
    ids=["no-tenant", "no-actor", "no-role", "bad-tenant", "bad-actor"],
)
async def test_partial_or_malformed_identity_is_400(
    client_factory: ClientFactory, headers: dict[str, str]
) -> None:
    async with client_factory(headers=headers) as client:
        response = await client.get("/api/v1/departments")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_identity"


async def test_unknown_role_fails_closed_with_403(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(identity_headers(role="ceo")) as client:
        response = await client.get("/api/v1/departments")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_role_header_is_case_insensitive(client_factory: ClientFactory) -> None:
    async with client_factory(identity_headers(role="ADMIN")) as client:
        response = await client.post(
            "/api/v1/departments", json={"name": "Engineering"}
        )

    assert response.status_code == 201
