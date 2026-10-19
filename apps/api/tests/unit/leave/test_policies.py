"""Unit tests for the Leave role matrix (`.ai/security.md`, T4)."""

import pytest

from app.domains.leave.policies import ensure_can_decide
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.leave.conftest import identity


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_decider_roles_are_allowed(role: Role) -> None:
    ensure_can_decide(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
def test_non_decider_roles_are_denied(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_decide(identity(role=role))
