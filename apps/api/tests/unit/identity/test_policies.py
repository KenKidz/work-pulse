"""Unit tests for the Identity role policy (`.ai/security.md` matrix)."""

import pytest

from app.domains.identity.policies import ensure_can_manage_users
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.identity.conftest import identity


@pytest.mark.parametrize("role", [Role.ADMIN, Role.SUPER_ADMIN])
def test_admin_roles_may_manage_users(role: Role) -> None:
    ensure_can_manage_users(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.HR, Role.FINANCE])
def test_every_other_role_is_denied(role: Role) -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_manage_users(identity(role=role))

    assert (exc.value.status_code, exc.value.code) == (403, "forbidden")
