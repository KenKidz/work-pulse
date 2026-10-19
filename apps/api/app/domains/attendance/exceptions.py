"""Domain errors — one subclass per pinned error code/status.

Codes and statuses are pinned in `.ai/features/attendance.md`
(## API contract → Pinned details → Error code → status). Messages are static
strings and never echo field values or note text (`.ai/security.md`).
"""

from app.shared.errors import WorkPulseError


class AttendanceAlreadyCheckedInError(WorkPulseError):
    status_code = 409
    code = "attendance_already_checked_in"

    def __init__(self) -> None:
        super().__init__("Employee already has an open attendance record")


class AttendanceNotOpenError(WorkPulseError):
    status_code = 409
    code = "attendance_not_open"

    def __init__(self) -> None:
        super().__init__("Attendance record is not open")


class AttendanceRecordNotFoundError(WorkPulseError):
    status_code = 404
    code = "attendance_record_not_found"

    def __init__(self) -> None:
        super().__init__("Attendance record not found")


class EmployeeNotActiveError(WorkPulseError):
    status_code = 409
    code = "employee_not_active"

    def __init__(self) -> None:
        super().__init__("Employee is not active")


class EmployeeOnLeaveError(WorkPulseError):
    status_code = 409
    code = "employee_on_leave"

    def __init__(self) -> None:
        super().__init__("Employee is on approved leave")


class ShiftNotFoundError(WorkPulseError):
    status_code = 404
    code = "shift_not_found"

    def __init__(self) -> None:
        super().__init__("Shift not found")


class InvalidWorkModeError(WorkPulseError):
    status_code = 400
    code = "invalid_work_mode"

    def __init__(self) -> None:
        super().__init__("Work mode must be onsite or remote")


class ShiftTimeInvalidError(WorkPulseError):
    status_code = 422
    code = "shift_time_invalid"

    def __init__(self) -> None:
        super().__init__("Shift end time must be after start time")


class ShiftScopeTakenError(WorkPulseError):
    status_code = 409
    code = "shift_scope_taken"

    def __init__(self) -> None:
        super().__init__("A shift already exists for this scope")
