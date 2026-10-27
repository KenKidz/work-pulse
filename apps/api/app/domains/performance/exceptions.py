"""Domain errors — one subclass per pinned performance code/status (T2).

Codes and statuses are pinned in `.ai/features/performance.md` (## Plan →
Design pins). Messages are static strings and never echo goal titles, review
text, feedback comments, skill notes or ratings (`.ai/security.md`).

`EmployeeNotActiveError` and `EmployeeNotFoundError` are cross-domain stable
codes with one class per domain (overtime, leave and payroll already own their
copies) — deliberate, so every domain owns its errors. Foreign and unknown
nested ids collapse into the 404 classes; existence is never leaked as 403.
"""

from app.shared.errors import WorkPulseError


class GoalNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope goal."""

    status_code = 404
    code = "goal_not_found"

    def __init__(self) -> None:
        super().__init__("Goal not found")


class GoalNotDraftError(WorkPulseError):
    """`activate` from a state other than `draft` (including a lost race)."""

    status_code = 409
    code = "goal_not_draft"

    def __init__(self) -> None:
        super().__init__("Goal is not a draft")


class GoalNotActiveError(WorkPulseError):
    """`complete` from a state other than `active` (including a lost race)."""

    status_code = 409
    code = "goal_not_active"

    def __init__(self) -> None:
        super().__init__("Goal is not active")


class GoalTerminalError(WorkPulseError):
    """`update`/`cancel` on a completed or cancelled goal."""

    status_code = 409
    code = "goal_terminal"

    def __init__(self) -> None:
        super().__init__("Goal is completed or cancelled")


class ReviewNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope review (drafts included)."""

    status_code = 404
    code = "review_not_found"

    def __init__(self) -> None:
        super().__init__("Review not found")


class ReviewIncompleteError(WorkPulseError):
    """`complete` without a rating."""

    status_code = 409
    code = "review_incomplete"

    def __init__(self) -> None:
        super().__init__("Review is missing its rating")


class ReviewNotDraftError(WorkPulseError):
    """`update`/`cancel`/feedback on a completed or cancelled review."""

    status_code = 409
    code = "review_not_draft"

    def __init__(self) -> None:
        super().__init__("Review is not a draft")


class FeedbackProviderInvalidError(WorkPulseError):
    """A nomination that is not an active, eligible provider."""

    status_code = 409
    code = "feedback_provider_invalid"

    def __init__(self) -> None:
        super().__init__("Feedback provider is not valid for this review")


class FeedbackAlreadySubmittedError(WorkPulseError):
    """A provider's second submission, or removing a provider who filed."""

    status_code = 409
    code = "feedback_already_submitted"

    def __init__(self) -> None:
        super().__init__("Feedback has already been submitted")


class FeedbackAccessDeniedError(WorkPulseError):
    """The subject asked for feedback on their own review."""

    status_code = 403
    code = "feedback_access_denied"

    def __init__(self) -> None:
        super().__init__("Not allowed to view this feedback")


class SkillNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope skill."""

    status_code = 404
    code = "skill_not_found"

    def __init__(self) -> None:
        super().__init__("Skill not found")


class SkillNameTakenError(WorkPulseError):
    """Duplicate `(tenant_id, name)` — archived rows included."""

    status_code = 409
    code = "skill_name_taken"

    def __init__(self) -> None:
        super().__init__("Skill name is already taken")


class SkillArchivedError(WorkPulseError):
    """Attaching an archived skill to an employee."""

    status_code = 409
    code = "skill_archived"

    def __init__(self) -> None:
        super().__init__("Skill is archived")


class EmployeeSkillExistsError(WorkPulseError):
    """A second attach for the same `(employee_id, skill_id)`."""

    status_code = 409
    code = "employee_skill_exists"

    def __init__(self) -> None:
        super().__init__("Employee already has this skill")


class EmployeeNotActiveError(WorkPulseError):
    status_code = 409
    code = "employee_not_active"

    def __init__(self) -> None:
        super().__init__("Employee is not active")


class EmployeeNotFoundError(WorkPulseError):
    """A foreign or unknown `employee_id` (filter or nested id)."""

    status_code = 404
    code = "employee_not_found"

    def __init__(self) -> None:
        super().__init__("Employee not found")


class PerformanceDeniedError(WorkPulseError):
    """A role with no standing attempted a performance capability."""

    status_code = 403
    code = "performance_denied"

    def __init__(self) -> None:
        super().__init__("Not allowed to access performance")


class PerformanceValidationError(WorkPulseError):
    """A cross-field query rule the schema cannot express (422).

    Pinned for `feedback_for_me=true` combined with a `status` other than
    `draft`; the house validation shape (`{"error": {"code":
    "validation_error"}}`) and a message that never echoes submitted values.
    """

    status_code = 422
    code = "validation_error"

    def __init__(self, message: str = "Request validation failed") -> None:
        super().__init__(message)
