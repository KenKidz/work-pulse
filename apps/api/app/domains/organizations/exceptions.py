"""Domain errors — one subclass per pinned error code/status.

Codes and statuses are pinned in `.ai/features/organizations.md`
(## API contract → Pinned details → Error code → status).
"""

from app.shared.errors import WorkPulseError


class DepartmentNotFoundError(WorkPulseError):
    status_code = 404
    code = "department_not_found"

    def __init__(self) -> None:
        super().__init__("Department not found")


class DepartmentNameTakenError(WorkPulseError):
    status_code = 409
    code = "department_name_taken"

    def __init__(self) -> None:
        super().__init__("Department name already taken in this tenant")


class DepartmentCycleError(WorkPulseError):
    status_code = 400
    code = "department_cycle"

    def __init__(self) -> None:
        super().__init__("Department hierarchy would contain a cycle")


class DepartmentArchivedInUseError(WorkPulseError):
    status_code = 409
    code = "department_archived_in_use"

    def __init__(self) -> None:
        super().__init__("Department still has active employees")


class PositionNotFoundError(WorkPulseError):
    status_code = 404
    code = "position_not_found"

    def __init__(self) -> None:
        super().__init__("Position not found")


class PositionTitleTakenError(WorkPulseError):
    status_code = 409
    code = "position_title_taken"

    def __init__(self) -> None:
        super().__init__("Position title already taken in this tenant")


class LocationNotFoundError(WorkPulseError):
    status_code = 404
    code = "location_not_found"

    def __init__(self) -> None:
        super().__init__("Location not found")


class LocationNameTakenError(WorkPulseError):
    status_code = 409
    code = "location_name_taken"

    def __init__(self) -> None:
        super().__init__("Location name already taken in this tenant")


class InvalidTimezoneError(WorkPulseError):
    status_code = 422
    code = "invalid_timezone"

    def __init__(self) -> None:
        super().__init__("Timezone is not a valid IANA timezone")
