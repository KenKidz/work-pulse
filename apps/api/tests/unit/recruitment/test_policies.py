"""Unit tests for the Recruitment role matrix (`.ai/security.md`, T2)."""

import pytest

from app.domains.recruitment.exceptions import RecruitmentDeniedError
from app.domains.recruitment.policies import (
    ensure_can_manage,
    ensure_has_standing,
    is_management,
)
from app.shared.identity import Role
from tests.unit.recruitment.conftest import identity


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_management_roles_may_manage_recruitment(role: Role) -> None:
    ensure_can_manage(identity(role=role))
    ensure_has_standing(identity(role=role))
    assert is_management(identity(role=role)) is True


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_non_management_roles_may_not_manage_recruitment(role: Role) -> None:
    with pytest.raises(RecruitmentDeniedError):
        ensure_can_manage(identity(role=role))
    assert is_management(identity(role=role)) is False


def test_manager_has_scoped_standing_but_no_management() -> None:
    ensure_has_standing(identity(role=Role.MANAGER))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
def test_employee_and_finance_have_no_standing(role: Role) -> None:
    with pytest.raises(RecruitmentDeniedError):
        ensure_has_standing(identity(role=role))
