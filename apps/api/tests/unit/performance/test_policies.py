"""Unit tests for the Performance role matrix (`.ai/security.md`, T4).

Every capability row of `.ai/features/performance.md` → ## Roles & access is
covered: allowed roles pass, every other role raises the pinned 403, and the
never-self review rule refuses the actor's own employee id.
"""

from uuid import UUID

import pytest

from app.domains.performance.exceptions import (
    FeedbackAccessDeniedError,
    PerformanceDeniedError,
)
from app.domains.performance.policies import (
    ensure_can_manage_goals,
    ensure_can_manage_skills,
    ensure_can_nominate,
    ensure_can_record_skills,
    ensure_can_run_reviews,
    ensure_can_view_feedback,
    is_management,
)
from app.shared.identity import Role
from tests.unit.performance.conftest import EMPLOYEE, OTHER, identity

ALL_ROLES = (
    Role.EMPLOYEE,
    Role.MANAGER,
    Role.HR,
    Role.FINANCE,
    Role.ADMIN,
    Role.SUPER_ADMIN,
)
MANAGEMENT_ROLES = (Role.HR, Role.ADMIN, Role.SUPER_ADMIN)


def _assert_denied(exc: pytest.ExceptionInfo[PerformanceDeniedError]) -> None:
    assert exc.value.status_code == 403
    assert exc.value.code == "performance_denied"


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_management_roles_are_management(role: Role) -> None:
    assert is_management(identity(role=role)) is True


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_other_roles_are_not_management(role: Role) -> None:
    assert is_management(identity(role=role)) is False


# --- goals -------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [Role.EMPLOYEE, Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN],
)
def test_goal_roles_may_manage_goals(role: Role) -> None:
    ensure_can_manage_goals(identity(role=role))


def test_finance_may_not_manage_goals() -> None:
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_manage_goals(identity(role=Role.FINANCE))
    _assert_denied(exc)


# --- reviews -----------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_review_roles_may_run_reviews(role: Role) -> None:
    ensure_can_run_reviews(identity(role=role, employee_id=EMPLOYEE), OTHER)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
def test_employee_and_finance_may_not_run_reviews(role: Role) -> None:
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_run_reviews(identity(role=role), OTHER)
    _assert_denied(exc)


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
def test_never_self_review_even_for_management(role: Role) -> None:
    """`employee_id` equal to the actor's own link is `403` in every review role."""
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_run_reviews(identity(role=role, employee_id=EMPLOYEE), EMPLOYEE)
    _assert_denied(exc)


def test_review_guard_without_subject_is_a_capability_check() -> None:
    ensure_can_run_reviews(identity(role=Role.MANAGER, employee_id=EMPLOYEE))


@pytest.mark.parametrize("role", MANAGEMENT_ROLES)
def test_management_roles_may_nominate_on_any_review(role: Role) -> None:
    ensure_can_nominate(identity(role=role), is_author=False)


def test_manager_may_nominate_only_on_their_own_review() -> None:
    ensure_can_nominate(identity(role=Role.MANAGER), is_author=True)
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_nominate(identity(role=Role.MANAGER), is_author=False)
    _assert_denied(exc)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
@pytest.mark.parametrize("is_author", [False, True])
def test_employee_and_finance_may_not_nominate(role: Role, is_author: bool) -> None:
    """A stale author flag never grants a role that cannot author reviews."""
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_nominate(identity(role=role), is_author=is_author)
    _assert_denied(exc)


# --- skill catalog and employee skills ---------------------------------------


@pytest.mark.parametrize("role", MANAGEMENT_ROLES)
def test_management_roles_manage_the_catalog(role: Role) -> None:
    ensure_can_manage_skills(identity(role=role))


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_non_management_roles_may_not_manage_the_catalog(role: Role) -> None:
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_manage_skills(identity(role=role))
    _assert_denied(exc)


@pytest.mark.parametrize(
    "role",
    [Role.EMPLOYEE, Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN],
)
def test_skill_recording_roles(role: Role) -> None:
    ensure_can_record_skills(identity(role=role))


def test_finance_may_not_record_skills() -> None:
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_record_skills(identity(role=Role.FINANCE))
    _assert_denied(exc)


# --- feedback ----------------------------------------------------------------


@pytest.mark.parametrize("role", ALL_ROLES)
def test_subject_never_sees_feedback(role: Role) -> None:
    """Subject wins over every other standing — no rating/comment leak."""
    with pytest.raises(FeedbackAccessDeniedError) as exc:
        ensure_can_view_feedback(
            identity(role=role),
            is_author=False,
            is_provider=False,
            is_subject=True,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "feedback_access_denied"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_author_sees_feedback(role: Role) -> None:
    ensure_can_view_feedback(
        identity(role=role),
        is_author=True,
        is_provider=False,
        is_subject=False,
    )


@pytest.mark.parametrize("role", ALL_ROLES)
def test_nominated_provider_sees_feedback(role: Role) -> None:
    """Any role may be nominated, including Employee and Finance."""
    ensure_can_view_feedback(
        identity(role=role),
        is_author=False,
        is_provider=True,
        is_subject=False,
    )


@pytest.mark.parametrize("role", MANAGEMENT_ROLES)
def test_hr_admin_super_admin_see_all_feedback(role: Role) -> None:
    ensure_can_view_feedback(
        identity(role=role),
        is_author=False,
        is_provider=False,
        is_subject=False,
    )


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
def test_uninvolved_roles_are_denied_feedback(role: Role) -> None:
    """A manager who neither authored nor was nominated has no standing."""
    with pytest.raises(PerformanceDeniedError) as exc:
        ensure_can_view_feedback(
            identity(role=role),
            is_author=False,
            is_provider=False,
            is_subject=False,
        )
    _assert_denied(exc)


def test_subject_flag_cannot_be_masked_by_another_employee_id() -> None:
    """The subject check uses the review's subject, not the caller's role."""
    subject: UUID = EMPLOYEE
    with pytest.raises(FeedbackAccessDeniedError):
        ensure_can_view_feedback(
            identity(role=Role.HR, employee_id=subject),
            is_author=True,
            is_provider=True,
            is_subject=True,
        )
