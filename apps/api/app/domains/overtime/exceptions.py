"""Domain errors — one subclass per pinned error code/status (T4).

Codes and statuses are pinned in `.ai/features/overtime.md`
(## API contract → "Pinned error codes"). Messages are static strings and never
echo dates, hours, reason or note text (`.ai/security.md`).

`EmployeeNotActiveError` and `EmployeeNotFoundError` are cross-domain stable
codes with one class per domain (attendance, leave and payroll already own their
copies) — deliberate, so every domain owns its errors and no shared error class
is introduced. `employee_not_found` here is the pinned 404 for a foreign or
unknown `employee_id` list filter; an out-of-scope request id uses
`overtime_request_not_found` instead so existence is never leaked.
"""

from app.shared.errors import WorkPulseError


class OvertimeOverlapsError(WorkPulseError):
    status_code = 409
    code = "overtime_overlaps"

    def __init__(self) -> None:
        super().__init__("Overtime request overlaps an open request on that date")


class OvertimeWorkDateInFutureError(WorkPulseError):
    status_code = 409
    code = "overtime_work_date_in_future"

    def __init__(self) -> None:
        super().__init__("Overtime work date is in the future")


class OvertimeNotRequestedError(WorkPulseError):
    """`approve` from a state other than `requested` (including a lost race)."""

    status_code = 409
    code = "overtime_not_requested"

    def __init__(self) -> None:
        super().__init__("Overtime request is not awaiting manager approval")


class OvertimeNotManagerApprovedError(WorkPulseError):
    """`verify` from a state other than `manager_approved` (including a race)."""

    status_code = 409
    code = "overtime_not_manager_approved"

    def __init__(self) -> None:
        super().__init__("Overtime request is not awaiting HR verification")


class OvertimeNotOpenError(WorkPulseError):
    """Withdraw after verification, or reject/withdraw a terminal row."""

    status_code = 409
    code = "overtime_not_open"

    def __init__(self) -> None:
        super().__init__("Overtime request is not open for this transition")


class EmployeeNotActiveError(WorkPulseError):
    status_code = 409
    code = "employee_not_active"

    def __init__(self) -> None:
        super().__init__("Employee is not active")


class OvertimeRequestNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope request — existence is not leaked."""

    status_code = 404
    code = "overtime_request_not_found"

    def __init__(self) -> None:
        super().__init__("Overtime request not found")


class EmployeeNotFoundError(WorkPulseError):
    """A foreign or unknown `employee_id` list filter (domain-local code)."""

    status_code = 404
    code = "employee_not_found"

    def __init__(self) -> None:
        super().__init__("Employee not found")
