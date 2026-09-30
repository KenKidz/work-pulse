"""AC3 + the roles matrix — role enforcement at the HTTP edge, fail-closed
session handling.

Role tests inject an explicit identity through the `get_identity` dependency
override; the fail-closed tests leave the real session resolver in place (no
override) so the cookie → session → user path runs for real.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.identity.credentials import (
    hash_password,
    new_session_token,
    session_token_hash,
)
from app.infrastructure.postgres.repositories.identity import (
    PostgresSessionRepository,
    PostgresUserRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    TENANT_A,
    USER,
    ClientFactory,
    identity,
)

READ_PATHS = ["/api/v1/departments", "/api/v1/positions", "/api/v1/locations"]

UNAUTHENTICATED_BODY = {
    "error": {"code": "unauthenticated", "message": "Authentication required"}
}


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_non_manage_roles_cannot_create(
    client_factory: ClientFactory, role: Role
) -> None:
    async with client_factory(identity(role=role)) as client:
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


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_non_manage_roles_cannot_archive(
    client_factory: ClientFactory, role: Role
) -> None:
    async with client_factory(identity(role=Role.ADMIN)) as admin:
        created = (
            await admin.post("/api/v1/departments", json={"name": "Engineering"})
        ).json()

    async with client_factory(identity(role=role)) as client:
        response = await client.post(f"/api/v1/departments/{created['id']}/archive")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", list(Role))
async def test_every_valid_role_can_read(
    client_factory: ClientFactory, role: Role
) -> None:
    async with client_factory(identity(role=role)) as client:
        for path in READ_PATHS:
            response = await client.get(path)
            assert response.status_code == 200, f"{role} GET {path}"


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
async def test_manage_roles_can_create(
    client_factory: ClientFactory, role: Role
) -> None:
    async with client_factory(identity(role=role)) as client:
        response = await client.post(
            "/api/v1/departments", json={"name": f"Dept {role}"}
        )

    assert response.status_code == 201


async def test_hr_create_and_admin_archive(client_factory: ClientFactory) -> None:
    async with client_factory(identity(role=Role.HR)) as hr:
        created = await hr.post("/api/v1/departments", json={"name": "Engineering"})
    assert created.status_code == 201

    async with client_factory(identity(role=Role.ADMIN)) as admin:
        archived = await admin.post(
            f"/api/v1/departments/{created.json()['id']}/archive",
            json={"reason": "reorg"},
        )
    assert archived.status_code == 200


# --- fail-closed session handling (no override: the real resolver runs) -------

PROTECTED_REQUESTS = [
    ("GET", "/api/v1/departments", None),
    ("GET", "/api/v1/positions", None),
    ("GET", "/api/v1/locations", None),
    ("GET", "/api/v1/employees", None),
    ("GET", "/api/v1/employees/me", None),
    ("GET", "/api/v1/attendance/records", None),
    ("GET", "/api/v1/attendance/board", None),
    ("GET", "/api/v1/shifts", None),
    ("GET", "/api/v1/auth/me", None),
    ("GET", "/api/v1/users", None),
    (
        "POST",
        "/api/v1/users",
        {"email": "x@y.test", "password": "long-enough-password", "role": "employee"},
    ),
    ("GET", f"/api/v1/users/{USER}", None),
    ("PATCH", f"/api/v1/users/{USER}", {"email": "x@y.test"}),
    ("POST", f"/api/v1/users/{USER}/activate", None),
    ("POST", f"/api/v1/users/{USER}/deactivate", None),
    (
        "POST",
        f"/api/v1/users/{USER}/password",
        {"new_password": "long-enough-password"},
    ),
    ("POST", "/api/v1/departments", {"name": "X"}),
    ("POST", "/api/v1/attendance/check-ins", {"work_mode": "onsite"}),
    (
        "PUT",
        "/api/v1/auth/password",
        {"current_password": "x", "new_password": "long-enough-password"},
    ),
]


async def test_unusable_sessions_are_401_on_every_protected_route(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC3: missing / unknown / expired session → `401 unauthenticated`."""
    user = await PostgresUserRepository(session).add(
        TENANT_A,
        uuid7(),
        email="ada@workpulse.test",
        role=Role.HR,
        employee_id=None,
        password_hash=await hash_password("irrelevant-password"),
        active=True,
    )
    expired_token = new_session_token()
    await PostgresSessionRepository(session).add(
        TENANT_A,
        uuid7(),
        user_id=user.id,
        token_hash=session_token_hash(expired_token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    for token in (None, "not-a-real-token", expired_token):
        async with client_factory(None) as client:
            if token is not None:
                client.cookies.set("wp_session", token)
            for method, path, body in PROTECTED_REQUESTS:
                response = await client.request(method, path, json=body)
                assert response.status_code == 401, f"{token=} {method} {path}"
                assert response.json() == UNAUTHENTICATED_BODY, f"{method} {path}"


async def test_dev_identity_headers_alone_no_longer_authenticate(
    client_factory: ClientFactory,
) -> None:
    """AC14: the retired dev-identity headers carry no authority."""
    headers = {
        "X-Tenant-Id": str(TENANT_A),
        "X-Actor-Id": str(USER),
        "X-Role": "admin",
    }

    async with client_factory(None) as client:
        response = await client.get("/api/v1/departments", headers=headers)
        assert response.status_code == 401
        assert response.json() == UNAUTHENTICATED_BODY
