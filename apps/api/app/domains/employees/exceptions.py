"""Domain errors — one subclass per pinned error code/status.

Codes and statuses are pinned in `.ai/features/employees.md`
(## API contract → Pinned details → Error code → status). Messages are static
strings and never echo field values (`.ai/security.md`).
"""

from app.shared.errors import WorkPulseError


class EmployeeNotFoundError(WorkPulseError):
    status_code = 404
    code = "employee_not_found"

    def __init__(self) -> None:
        super().__init__("Employee not found")


class EmployeeEmailTakenError(WorkPulseError):
    status_code = 409
    code = "employee_email_taken"

    def __init__(self) -> None:
        super().__init__("Employee email already taken in this tenant")


class EmployeeCodeTakenError(WorkPulseError):
    status_code = 409
    code = "employee_code_taken"

    def __init__(self) -> None:
        super().__init__("Employee code already taken in this tenant")


class EmployeeManagerInvalidError(WorkPulseError):
    status_code = 400
    code = "employee_manager_invalid"

    def __init__(self) -> None:
        super().__init__("Manager must be an active employee of this tenant")


class EmployeeSelfManagerError(WorkPulseError):
    status_code = 400
    code = "employee_self_manager"

    def __init__(self) -> None:
        super().__init__("An employee cannot be their own manager")


class EmployeeAlreadyTerminatedError(WorkPulseError):
    status_code = 409
    code = "employee_already_terminated"

    def __init__(self) -> None:
        super().__init__("Employee is already terminated")


class EmploymentPeriodOverlapError(WorkPulseError):
    status_code = 409
    code = "employment_period_overlap"

    def __init__(self) -> None:
        super().__init__("Employment period would overlap an existing period")


class EmployeeTransferEffectiveInvalidError(WorkPulseError):
    status_code = 422
    code = "employee_transfer_effective_invalid"

    def __init__(self) -> None:
        super().__init__("Transfer effective date is invalid")


class TransferTargetArchivedError(WorkPulseError):
    status_code = 409
    code = "transfer_target_archived"

    def __init__(self) -> None:
        super().__init__("Assignment target is archived")
