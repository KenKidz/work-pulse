"""Unit tests for the Payroll role matrix (`.ai/features/payroll.md`)."""

import pytest

from app.domains.payroll.exceptions import (
    PayrollRunNotFoundError,
    PayslipNotFoundError,
)
from app.domains.payroll.policies import (
    ensure_can_configure,
    ensure_can_run,
    ensure_can_view_payslip,
    ensure_can_view_runs,
)
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.payroll.conftest import EMPLOYEE, OTHER, identity

OPERATOR_ROLES = [Role.FINANCE, Role.ADMIN, Role.SUPER_ADMIN]
VIEW_ROLES = [Role.HR, Role.FINANCE, Role.ADMIN, Role.SUPER_ADMIN]
NON_OPERATOR_ROLES = [Role.EMPLOYEE, Role.MANAGER, Role.HR]


@pytest.mark.parametrize("role", OPERATOR_ROLES)
def test_operator_roles_may_configure(role: Role) -> None:
    ensure_can_configure(identity(role=role))


@pytest.mark.parametrize("role", NON_OPERATOR_ROLES)
def test_non_operator_roles_may_not_configure(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_configure(identity(role=role))


@pytest.mark.parametrize("role", OPERATOR_ROLES)
def test_operator_roles_may_run(role: Role) -> None:
    ensure_can_run(identity(role=role))


@pytest.mark.parametrize("role", NON_OPERATOR_ROLES)
def test_non_operator_roles_may_not_run(role: Role) -> None:
    """HR is deliberately view-only: an HR finalize is a 403, not a 404."""
    with pytest.raises(ForbiddenError):
        ensure_can_run(identity(role=role))


@pytest.mark.parametrize("role", VIEW_ROLES)
def test_view_roles_may_read_runs(role: Role) -> None:
    ensure_can_view_runs(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER])
def test_employee_and_manager_get_not_found_for_runs(role: Role) -> None:
    """Out-of-scope viewers get 404 so existence is never leaked."""
    with pytest.raises(PayrollRunNotFoundError):
        ensure_can_view_runs(identity(role=role))


@pytest.mark.parametrize("role", VIEW_ROLES)
def test_view_roles_may_read_any_payslip(role: Role) -> None:
    ensure_can_view_payslip(identity(role=role, employee_id=EMPLOYEE), OTHER)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER])
def test_own_payslip_is_readable_for_every_role(role: Role) -> None:
    ensure_can_view_payslip(identity(role=role, employee_id=EMPLOYEE), EMPLOYEE)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER])
def test_foreign_payslip_is_not_found(role: Role) -> None:
    with pytest.raises(PayslipNotFoundError):
        ensure_can_view_payslip(identity(role=role, employee_id=EMPLOYEE), OTHER)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER])
def test_linkless_account_cannot_read_a_payslip(role: Role) -> None:
    """Fail closed: no employee link means "own" can never match."""
    with pytest.raises(PayslipNotFoundError):
        ensure_can_view_payslip(identity(role=role, employee_id=None), EMPLOYEE)
