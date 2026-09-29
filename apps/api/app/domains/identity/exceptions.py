"""Domain errors — one subclass per pinned error code/status.

Codes and statuses are pinned in `.ai/features/identity-access.md`
(## API contract → Pinned details → Error code → status). Messages are static
strings and never echo field values; password material never appears in any
message (`.ai/security.md`).
"""

# The session resolver raises `UnauthenticatedError`; it is cross-cutting
# plumbing and lives in `app/shared/errors.py` (T4), re-exported here so the
# domain's error surface stays in one place.
from app.shared.errors import UnauthenticatedError as UnauthenticatedError
from app.shared.errors import WorkPulseError


class UserNotFoundError(WorkPulseError):
    status_code = 404
    code = "user_not_found"

    def __init__(self) -> None:
        super().__init__("User not found")


class InvalidCredentialsError(WorkPulseError):
    """Wrong password and unknown email collapse into this exact failure."""

    status_code = 401
    code = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__("Invalid email or password")


class InvalidPasswordError(WorkPulseError):
    status_code = 400
    code = "invalid_password"

    def __init__(self) -> None:
        super().__init__("Current password is incorrect")


class UserEmailTakenError(WorkPulseError):
    status_code = 409
    code = "user_email_taken"

    def __init__(self) -> None:
        super().__init__("User email already taken")


class EmployeeLinkTakenError(WorkPulseError):
    status_code = 409
    code = "employee_link_taken"

    def __init__(self) -> None:
        super().__init__("Employee is already linked to a user")


class LastAdminError(WorkPulseError):
    status_code = 409
    code = "last_admin"

    def __init__(self) -> None:
        super().__init__("Cannot deactivate the last active admin")
