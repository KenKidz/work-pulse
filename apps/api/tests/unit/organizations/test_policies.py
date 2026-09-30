"""Unit tests for the organizations policies (`.ai/security.md` matrix)."""

from uuid import UUID

import pytest

from app.domains.organizations.policies import ensure_can_manage, ensure_can_read
from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

TENANT = UUID("00000000-0000-4000-8000-000000000001")
ACTOR = UUID("00000000-0000-4000-8000-000000000002")


def make_identity(role: Role) -> Identity:
    return Identity(tenant_id=TENANT, actor_id=ACTOR, role=role)


@pytest.mark.parametrize("role", list(Role))
def test_read_allowed_for_every_valid_role(role: Role) -> None:
    ensure_can_read(make_identity(role))  # must not raise


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_manage_allowed_for_hr_admin_super_admin(role: Role) -> None:
    ensure_can_manage(make_identity(role))  # must not raise


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_manage_denied_for_employee_manager_finance(role: Role) -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_manage(make_identity(role))

    assert exc.value.code == "forbidden"
    assert exc.value.status_code == 403
