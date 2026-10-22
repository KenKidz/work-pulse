"""Unit tests for the Overtime two-step role matrix (`.ai/security.md`, T4)."""

import pytest

from app.domains.overtime.policies import ensure_can_approve, ensure_can_verify
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.overtime.conftest import identity


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_manager_step_allows_manager_hr_and_admin_roles(role: Role) -> None:
    ensure_can_approve(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
def test_manager_step_denies_employee_and_finance(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_approve(identity(role=role))


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_hr_step_allows_hr_and_admin_roles(role: Role) -> None:
    ensure_can_verify(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE, Role.MANAGER])
def test_hr_step_denies_manager_without_an_hr_role(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_verify(identity(role=role))
