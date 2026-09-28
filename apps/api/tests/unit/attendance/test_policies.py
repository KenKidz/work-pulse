"""Unit tests for the Attendance role matrix (`.ai/security.md`, T1)."""

import pytest

from app.domains.attendance.policies import (
    ensure_can_manage_shifts,
    ensure_can_read_board,
    ensure_can_read_records,
)
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.attendance.conftest import identity


@pytest.mark.parametrize("role", list(Role))
def test_every_valid_role_can_read_records(role: Role) -> None:
    """Records are readable by all; employee/finance are scoped in the service."""
    ensure_can_read_records(identity(role=role))


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_board_roles_are_allowed(role: Role) -> None:
    ensure_can_read_board(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
def test_board_roles_are_denied(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_read_board(identity(role=role))


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_shift_manage_roles_are_allowed(role: Role) -> None:
    ensure_can_manage_shifts(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_shift_manage_roles_are_denied(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_manage_shifts(identity(role=role))
