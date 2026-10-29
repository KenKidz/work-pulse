"""Wire-shape and pinned-error tests for the Documents domain (T1).

The contract under test is `.ai/features/employee-documents.md` (`## API
contract`): exact request/response fields, the category enum, the title/size/
filename bounds, the at-least-one PATCH rule, and the stable error-code ↔
status pairs. Deleted rows never serialize — there is no `deleted_at` field.

The MIME allowlist and the 20 MB cap are deliberately **not** schema-rejected:
the service checks them so violations carry the pinned
`document_type_not_allowed` / `document_too_large` codes instead of the
generic `validation_error` (T4).
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.documents.exceptions import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    DocumentTypeNotAllowedError,
    DocumentUploadNotCompletedError,
    EmployeeNotFoundError,
)
from app.domains.documents.schemas import (
    ALLOWED_DOCUMENT_CONTENT_TYPES,
    MAX_DOCUMENT_SIZE_BYTES,
    Document,
    DocumentCategory,
    DocumentCreate,
    DocumentDownload,
    DocumentUpdate,
    DocumentUpload,
    DocumentUploadCreate,
)
from app.shared.errors import WorkPulseError

DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000003")
UPLOAD_ID = UUID("00000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)

PINNED_ERRORS = [
    (DocumentTypeNotAllowedError, 422, "document_type_not_allowed"),
    (DocumentTooLargeError, 422, "document_too_large"),
    (DocumentUploadNotCompletedError, 409, "document_upload_not_completed"),
    (DocumentNotFoundError, 404, "document_not_found"),
    (EmployeeNotFoundError, 404, "employee_not_found"),
]


def _document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "id": DOCUMENT_ID,
        "employee_id": EMPLOYEE_ID,
        "title": "Signed contract",
        "category": DocumentCategory.CONTRACT,
        "expiry_date": date(2027, 12, 31),
        "filename": "contract.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "uploaded_by": ACTOR_ID,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Document.model_validate(values)


def test_upload_protocol_constants_are_pinned() -> None:
    """The cap is 20 MB and the MIME allowlist is exactly the three types."""
    assert MAX_DOCUMENT_SIZE_BYTES == 20 * 1024 * 1024
    assert ALLOWED_DOCUMENT_CONTENT_TYPES == {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }


def test_upload_claim_accepts_the_size_bounds() -> None:
    claim = DocumentUploadCreate(content_type="application/pdf", size_bytes=1)
    assert claim.size_bytes == 1

    at_cap = DocumentUploadCreate(
        content_type="image/png", size_bytes=MAX_DOCUMENT_SIZE_BYTES
    )
    assert at_cap.size_bytes == MAX_DOCUMENT_SIZE_BYTES


@pytest.mark.parametrize("size_bytes", [0, -1])
def test_upload_claim_requires_a_positive_size(size_bytes: int) -> None:
    with pytest.raises(ValidationError):
        DocumentUploadCreate(content_type="application/pdf", size_bytes=size_bytes)


def test_upload_claim_defers_allowlist_and_cap_to_the_service() -> None:
    """Disallowed claims reach the service, which raises the pinned 422 code.

    A schema-level `Literal`/`le=` would pre-empt the service with the generic
    `validation_error` code and break the pinned contract.
    """
    claim = DocumentUploadCreate(
        content_type="text/html", size_bytes=MAX_DOCUMENT_SIZE_BYTES + 1
    )

    assert claim.content_type == "text/html"
    assert claim.size_bytes == MAX_DOCUMENT_SIZE_BYTES + 1

    # The pinned bounds still appear in the generated contract.
    schema = DocumentUploadCreate.model_json_schema()["properties"]
    assert schema["content_type"]["enum"] == sorted(ALLOWED_DOCUMENT_CONTENT_TYPES)
    assert schema["size_bytes"]["maximum"] == MAX_DOCUMENT_SIZE_BYTES


def test_upload_claim_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DocumentUploadCreate.model_validate(
            {
                "content_type": "application/pdf",
                "size_bytes": 1024,
                "filename": "contract.pdf",
            }
        )


def test_confirm_requires_upload_id_title_and_category() -> None:
    with pytest.raises(ValidationError):
        DocumentCreate.model_validate({})
    with pytest.raises(ValidationError):
        DocumentCreate.model_validate({"title": "Signed contract"})
    with pytest.raises(ValidationError):
        DocumentCreate.model_validate(
            {"upload_id": str(UPLOAD_ID), "title": "Signed contract"}
        )


def test_confirm_does_not_accept_a_client_filename() -> None:
    """The filename comes from the staged object's metadata (ADR 0022)."""
    with pytest.raises(ValidationError):
        DocumentCreate.model_validate(
            {
                "upload_id": str(UPLOAD_ID),
                "title": "Signed contract",
                "category": "contract",
                "filename": "contract.pdf",
            }
        )


@pytest.mark.parametrize("title", ["", "x" * 201])
def test_title_is_bounded_to_one_to_two_hundred_characters(title: str) -> None:
    with pytest.raises(ValidationError):
        DocumentCreate(
            upload_id=UPLOAD_ID,
            title=title,
            category=DocumentCategory.CONTRACT,
        )


def test_title_at_the_upper_bound_is_accepted() -> None:
    title = "x" * 200
    assert (
        DocumentCreate(
            upload_id=UPLOAD_ID,
            title=title,
            category=DocumentCategory.CONTRACT,
        ).title
        == title
    )


@pytest.mark.parametrize("title", ["a\x00b", "\x00"])
def test_title_rejects_nul_characters(title: str) -> None:
    """Postgres text cannot store NUL; the wire boundary rejects it first."""
    with pytest.raises(ValidationError):
        DocumentCreate(
            upload_id=UPLOAD_ID,
            title=title,
            category=DocumentCategory.CONTRACT,
        )
    with pytest.raises(ValidationError):
        DocumentUpdate(title=title)


def test_category_enum_is_pinned() -> None:
    assert {category.value for category in DocumentCategory} == {
        "contract",
        "identification",
        "certification",
        "other",
    }

    for category in DocumentCategory:
        created = DocumentCreate(
            upload_id=UPLOAD_ID, title="Document", category=category
        )
        assert created.category is category

    with pytest.raises(ValidationError):
        DocumentCreate.model_validate(
            {
                "upload_id": str(UPLOAD_ID),
                "title": "Document",
                "category": "passport",
            }
        )


def test_expiry_date_is_an_iso_date() -> None:
    created = DocumentCreate.model_validate(
        {
            "upload_id": str(UPLOAD_ID),
            "title": "Certificate",
            "category": "certification",
            "expiry_date": "2027-03-31",
        }
    )
    assert created.expiry_date == date(2027, 3, 31)

    with pytest.raises(ValidationError):
        DocumentCreate.model_validate(
            {
                "upload_id": str(UPLOAD_ID),
                "title": "Certificate",
                "category": "certification",
                "expiry_date": "31/03/2027",
            }
        )


def test_document_wire_shape_is_pinned_and_omits_deleted_at() -> None:
    assert set(Document.model_fields) == {
        "id",
        "employee_id",
        "title",
        "category",
        "expiry_date",
        "filename",
        "content_type",
        "size_bytes",
        "uploaded_by",
        "created_at",
        "updated_at",
    }


def test_filename_is_bounded_to_two_hundred_and_fifty_five_characters() -> None:
    assert _document(filename="x" * 255).filename == "x" * 255

    with pytest.raises(ValidationError):
        _document(filename="x" * 256)


def test_upload_and_download_wire_shapes_are_pinned() -> None:
    assert set(DocumentUpload.model_fields) == {
        "upload_id",
        "url",
        "fields",
        "expires_at",
    }
    assert set(DocumentDownload.model_fields) == {"url", "expires_at"}


def test_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        DocumentUpdate()


def test_update_accepts_single_fields() -> None:
    assert DocumentUpdate(title="Renamed").model_fields_set == {"title"}
    assert (
        DocumentUpdate(category=DocumentCategory.OTHER).category
        is DocumentCategory.OTHER
    )
    assert DocumentUpdate(expiry_date=date(2028, 1, 1)).model_fields_set == {
        "expiry_date"
    }


def test_update_rejects_null_title_and_category_but_clears_expiry() -> None:
    with pytest.raises(ValidationError):
        DocumentUpdate(title=None)
    with pytest.raises(ValidationError):
        DocumentUpdate(category=None)

    cleared = DocumentUpdate(expiry_date=None)
    assert cleared.expiry_date is None
    assert cleared.model_fields_set == {"expiry_date"}


def test_update_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DocumentUpdate.model_validate({"filename": "renamed.pdf"})


@pytest.mark.parametrize(("error_class", "status", "code"), PINNED_ERRORS)
def test_error_carries_the_pinned_code_and_status(
    error_class: type[WorkPulseError], status: int, code: str
) -> None:
    error = error_class()

    assert error.status_code == status
    assert error.code == code
    assert error.message
    assert str(error) == error.message
