"""Unit tests for the Employees role matrix (`.ai/security.md`)."""

import pytest

from app.domains.employees.policies import (
    ensure_can_manage_employees,
    ensure_can_read_employees,
    ensure_can_read_self,
)
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.employees.conftest import identity


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_manage_roles_are_allowed(role: Role) -> None:
    ensure_can_manage_employees(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_manage_roles_are_denied(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_manage_employees(identity(role=role))


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN, Role.MANAGER])
def test_read_roles_are_allowed(role: Role) -> None:
    ensure_can_read_employees(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
def test_read_roles_are_denied(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_read_employees(identity(role=role))


@pytest.mark.parametrize("role", list(Role))
def test_every_valid_role_can_read_self(role: Role) -> None:
    ensure_can_read_self(identity(role=role))
