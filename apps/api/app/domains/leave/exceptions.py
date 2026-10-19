"""Domain errors — one subclass per pinned error code/status.

Codes and statuses are pinned in `.ai/features/leave.md`
(## API contract → "Pinned error codes"). Messages are static strings and never
echo leave dates, reason or decision-note text (`.ai/security.md`).

`EmployeeNotActiveError` is a cross-domain shared stable code with one class per
domain (attendance already owns its copy) — deliberate, so every domain owns its
errors; no shared error class is introduced.
"""

from app.shared.errors import WorkPulseError


class LeaveOverlapsError(WorkPulseError):
    status_code = 409
    code = "leave_overlaps"

    def __init__(self) -> None:
        super().__init__("Leave request overlaps an existing request")


class LeaveNotPendingError(WorkPulseError):
    status_code = 409
    code = "leave_not_pending"

    def __init__(self) -> None:
        super().__init__("Leave request is not pending")


class LeaveInsufficientBalanceError(WorkPulseError):
    status_code = 409
    code = "leave_insufficient_balance"

    def __init__(self) -> None:
        super().__init__("Leave request exceeds the remaining balance")


class LeaveStartTooOldError(WorkPulseError):
    status_code = 409
    code = "leave_start_too_old"

    def __init__(self) -> None:
        super().__init__("Leave request start date is too old")


class EmployeeNotActiveError(WorkPulseError):
    status_code = 409
    code = "employee_not_active"

    def __init__(self) -> None:
        super().__init__("Employee is not active")


class LeaveRequestNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope request — existence is not leaked."""

    status_code = 404
    code = "leave_request_not_found"

    def __init__(self) -> None:
        super().__init__("Leave request not found")


class LeaveTypeNotFoundError(WorkPulseError):
    status_code = 404
    code = "leave_type_not_found"

    def __init__(self) -> None:
        super().__init__("Leave type not found")
