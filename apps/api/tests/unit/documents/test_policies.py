"""Unit tests for the Documents role matrix (`.ai/security.md`, T3).

Every capability row of `.ai/features/employee-documents.md` → ## Roles &
access is covered: writes are HR/Admin/Super Admin only, and reads are the
owner (any role), a manager's resolved team scope, HR/Admin/Super Admin
everyone and Finance their own only. Every denied cell raises the house
`403 forbidden`; no documents-specific denial code exists.
"""

from collections.abc import Sequence
from uuid import UUID

import pytest

from app.domains.documents.policies import ensure_can_manage, ensure_can_read
from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
SELF = UUID("00000000-0000-4000-8000-000000000002")
REPORT = UUID("00000000-0000-4000-8000-000000000003")
OTHER = UUID("00000000-0000-4000-8000-000000000004")

ALL_ROLES = (
    Role.EMPLOYEE,
    Role.MANAGER,
    Role.HR,
    Role.FINANCE,
    Role.ADMIN,
    Role.SUPER_ADMIN,
)
MANAGEMENT_ROLES = (Role.HR, Role.ADMIN, Role.SUPER_ADMIN)
MANAGER_TEAM = (SELF, REPORT)


def identity(
    role: Role = Role.HR,
    *,
    employee_id: UUID | None = SELF,
    tenant_id: UUID = TENANT,
) -> Identity:
    """The acting user (`actor_id`) plus its optional employee link."""
    return Identity(
        tenant_id=tenant_id,
        actor_id=USER,
        role=role,
        employee_id=employee_id,
    )


def _assert_denied(exc: pytest.ExceptionInfo[ForbiddenError]) -> None:
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"


def _visible(manager: Identity) -> Sequence[UUID]:
    """The resolved manager scope used by the read guard in these cases."""
    return MANAGER_TEAM if manager.role is Role.MANAGER else ()


# --- writes ------------------------------------------------------------------


@pytest.mark.parametrize("role", MANAGEMENT_ROLES)
def test_management_roles_may_manage(role: Role) -> None:
    ensure_can_manage(identity(role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_non_management_roles_may_not_manage(role: Role) -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_manage(identity(role))
    _assert_denied(exc)


# --- reads: owner first ------------------------------------------------------


@pytest.mark.parametrize("role", ALL_ROLES)
def test_owner_may_read_own_documents_regardless_of_role(role: Role) -> None:
    ensure_can_read(identity(role), SELF, _visible(identity(role)))


def test_unlinked_employee_may_not_read_anyone() -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.EMPLOYEE, employee_id=None), SELF, ())
    _assert_denied(exc)


# --- reads: employee ---------------------------------------------------------


@pytest.mark.parametrize("target", [REPORT, OTHER])
def test_employee_may_not_read_another_employee(target: UUID) -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.EMPLOYEE), target, ())
    _assert_denied(exc)


def test_scope_does_not_grant_an_employee_role() -> None:
    """A stale/non-empty scope is only meaningful for the Manager role."""
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.EMPLOYEE), REPORT, MANAGER_TEAM)
    _assert_denied(exc)


# --- reads: manager ----------------------------------------------------------


def test_manager_may_read_self_and_direct_reports() -> None:
    manager = identity(Role.MANAGER)
    ensure_can_read(manager, SELF, MANAGER_TEAM)
    ensure_can_read(manager, REPORT, MANAGER_TEAM)


def test_manager_may_not_read_a_non_report() -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.MANAGER), OTHER, MANAGER_TEAM)
    _assert_denied(exc)


def test_unlinked_manager_sees_only_the_resolved_empty_scope() -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.MANAGER, employee_id=None), REPORT, ())
    _assert_denied(exc)


# --- reads: finance ----------------------------------------------------------


def test_finance_may_read_only_own() -> None:
    ensure_can_read(identity(Role.FINANCE), SELF, ())
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.FINANCE), OTHER, ())
    _assert_denied(exc)


def test_finance_gets_no_team_scope() -> None:
    with pytest.raises(ForbiddenError) as exc:
        ensure_can_read(identity(Role.FINANCE), REPORT, MANAGER_TEAM)
    _assert_denied(exc)


# --- reads: management -------------------------------------------------------


@pytest.mark.parametrize("role", MANAGEMENT_ROLES)
@pytest.mark.parametrize("target", [SELF, REPORT, OTHER])
def test_management_roles_read_everyone(role: Role, target: UUID) -> None:
    ensure_can_read(identity(role, employee_id=None), target, ())
