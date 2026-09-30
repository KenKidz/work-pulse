"""AC5 — only admin/super_admin manage users; every other role fails closed 403.

Authorization lives in `domains/identity/policies.py` (`ensure_can_manage_users`)
and is enforced at the router edge before any service call; `403 forbidden` for
every route of the user-management surface.
"""

from uuid import UUID

import pytest

from app.shared.identity import Role
from tests.integration.conftest import ClientFactory, identity

TARGET = UUID("00000000-0000-4000-8000-0000000000ff")

VALID_NEW_USER = {
    "email": "ada@workpulse.test",
    "password": "long-enough-password",
    "role": "employee",
}

USER_ROUTES = [
    ("GET", "/api/v1/users", None),
    ("POST", "/api/v1/users", VALID_NEW_USER),
    ("GET", f"/api/v1/users/{TARGET}", None),
    ("PATCH", f"/api/v1/users/{TARGET}", {"email": "augusta@workpulse.test"}),
    ("POST", f"/api/v1/users/{TARGET}/activate", None),
    ("POST", f"/api/v1/users/{TARGET}/deactivate", None),
    (
        "POST",
        f"/api/v1/users/{TARGET}/password",
        {"new_password": "long-enough-password"},
    ),
]


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.HR, Role.FINANCE])
async def test_non_admin_roles_get_403_on_every_users_route(
    client_factory: ClientFactory, role: Role
) -> None:
    async with client_factory(identity(role=role)) as client:
        for method, path, body in USER_ROUTES:
            response = await client.request(method, path, json=body)
            assert response.status_code == 403, f"{role} {method} {path}"
            assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.SUPER_ADMIN])
async def test_admin_roles_can_list_and_create(
    client_factory: ClientFactory, role: Role
) -> None:
    async with client_factory(identity(role=role)) as client:
        listed = await client.get("/api/v1/users")
        assert listed.status_code == 200

        created = await client.post(
            "/api/v1/users",
            json={**VALID_NEW_USER, "email": f"{role.value}@workpulse.test"},
        )
        assert created.status_code == 201
        assert created.json()["role"] == "employee"
