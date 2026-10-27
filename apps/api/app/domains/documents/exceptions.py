"""Domain errors — one subclass per pinned document code/status (T1).

Codes and statuses are pinned in `.ai/features/employee-documents.md`
(`## API contract` → Error codes). Messages are static strings and never echo
titles, filenames, storage keys or presigned URLs (`.ai/security.md`).

Authorization denials — including a same-tenant out-of-scope read — reuse
`app.shared.errors.ForbiddenError` (`403 forbidden`) so every domain reports
the house code. Foreign, unknown, deleted and cross-tenant nested ids collapse
into the 404 classes; a foreign id never leaks existence as 403.
`EmployeeNotFoundError` is a cross-domain stable code with one class per domain
(the house pattern) — deliberate, so every domain owns its errors.
"""

from app.shared.errors import WorkPulseError


class DocumentTypeNotAllowedError(WorkPulseError):
    """The claimed or staged MIME type is outside the allowlist."""

    status_code = 422
    code = "document_type_not_allowed"

    def __init__(self) -> None:
        super().__init__("Document type is not allowed")


class DocumentTooLargeError(WorkPulseError):
    """The claimed or staged object exceeds the 20 MB cap."""

    status_code = 422
    code = "document_too_large"

    def __init__(self) -> None:
        super().__init__("Document exceeds the size limit")


class DocumentUploadNotCompletedError(WorkPulseError):
    """No staged object for the `upload_id` (replay, expiry or missing metadata)."""

    status_code = 409
    code = "document_upload_not_completed"

    def __init__(self) -> None:
        super().__init__("Document upload is not completed")


class DocumentNotFoundError(WorkPulseError):
    """Unknown, deleted or cross-tenant document."""

    status_code = 404
    code = "document_not_found"

    def __init__(self) -> None:
        super().__init__("Document not found")


class EmployeeNotFoundError(WorkPulseError):
    """A foreign or unknown `employee_id` (path or nested id)."""

    status_code = 404
    code = "employee_not_found"

    def __init__(self) -> None:
        super().__init__("Employee not found")
