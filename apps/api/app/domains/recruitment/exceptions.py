"""Domain errors — one subclass per pinned recruitment code/status (T2).

Codes and statuses are pinned in `.ai/features/recruitment.md` (## API contract
→ "Pinned error codes"). Messages are static strings and never echo candidate
names, emails, phones, notes or feedback text (`.ai/security.md`).

The hire seam's errors (`employee_email_taken`, `employee_code_taken`,
`employee_manager_invalid`) and the job org-ref errors
(`department_not_found`, `position_not_found`, `location_not_found`) are owned
by Employees/Organizations and propagate unchanged (T4); recruitment must not
re-declare them or the seam's statuses would drift.
"""

from app.shared.errors import WorkPulseError


class JobNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope job — existence is not leaked."""

    status_code = 404
    code = "job_not_found"

    def __init__(self) -> None:
        super().__init__("Job not found")


class CandidateNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope candidate."""

    status_code = 404
    code = "candidate_not_found"

    def __init__(self) -> None:
        super().__init__("Candidate not found")


class InterviewNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope interview."""

    status_code = 404
    code = "interview_not_found"

    def __init__(self) -> None:
        super().__init__("Interview not found")


class InterviewerNotFoundError(WorkPulseError):
    """A foreign or unknown `interviewer_id` filter/assignment."""

    status_code = 404
    code = "interviewer_not_found"

    def __init__(self) -> None:
        super().__init__("Interviewer not found")


class JobNotDraftError(WorkPulseError):
    """`open` from a non-`draft` job (including a lost race)."""

    status_code = 409
    code = "job_not_draft"

    def __init__(self) -> None:
        super().__init__("Job is not a draft")


class JobNotOpenError(WorkPulseError):
    """`close`, `update`, candidate intake or hire on a non-open job."""

    status_code = 409
    code = "job_not_open"

    def __init__(self) -> None:
        super().__init__("Job is not open")


class JobOpeningsFilledError(WorkPulseError):
    """`hire` when hired candidates already reached the job's `openings`."""

    status_code = 409
    code = "job_openings_filled"

    def __init__(self) -> None:
        super().__init__("Job has no free openings")


class CandidateAlreadyAppliedError(WorkPulseError):
    """Duplicate `(tenant, job_id, email)` application."""

    status_code = 409
    code = "candidate_already_applied"

    def __init__(self) -> None:
        super().__init__("Candidate already applied to this job")


class CandidateStageConflictError(WorkPulseError):
    """`advance` with no next stage, wrong-state `hire`, or schedule."""

    status_code = 409
    code = "candidate_stage_conflict"

    def __init__(self) -> None:
        super().__init__("Candidate is not in the required stage")


class CandidateNotOpenError(WorkPulseError):
    """`reject`/`withdraw` on a terminal candidate."""

    status_code = 409
    code = "candidate_not_open"

    def __init__(self) -> None:
        super().__init__("Candidate is no longer open")


class InterviewNotScheduledError(WorkPulseError):
    """`update`/`complete`/`cancel` on a completed or cancelled interview."""

    status_code = 409
    code = "interview_not_scheduled"

    def __init__(self) -> None:
        super().__init__("Interview is not scheduled")


class InterviewerNotActiveError(WorkPulseError):
    """Assigning a terminated employee as interviewer."""

    status_code = 409
    code = "interviewer_not_active"

    def __init__(self) -> None:
        super().__init__("Interviewer is not an active employee")


class RecruitmentDeniedError(WorkPulseError):
    """A role with no standing attempted a recruitment capability."""

    status_code = 403
    code = "recruitment_denied"

    def __init__(self) -> None:
        super().__init__("Not allowed to access recruitment")


class HireDateInFutureError(WorkPulseError):
    """`hired_at > today` — service-level because the clock is injected."""

    status_code = 422
    code = "validation_error"

    def __init__(self) -> None:
        super().__init__("Hire date is in the future")
