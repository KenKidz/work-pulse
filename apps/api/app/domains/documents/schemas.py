"""Wire contracts for the Documents domain (T1).

The API contract is pinned by `.ai/features/employee-documents.md`
(`## API contract`). Every wire type lands here in T1 even though routes arrive
in T5: one vocabulary, one place to check against the pinned JSON.

The upload protocol is two-step. `DocumentUploadCreate` is the claim step: the
service checks the MIME allowlist and the 20 MB cap itself so violations carry
the pinned `document_type_not_allowed` / `document_too_large` codes instead of
the generic `validation_error`. `DocumentCreate` is the confirm step and
carries no filename/content-type/size: those come from the staged object's
`x-amz-meta-filename` / `x-amz-meta-claimed-size` metadata (ADR 0022).

PII (`.ai/security.md`): `title`, `filename` and storage keys/presigned URLs
are returned to authorized viewers only and are never logged, echoed in errors
or written into event payloads or audit `changes` values.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.shared.pagination import Page

#: The upload cap pinned by the feature spec (20 MB).
MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024


def _require_no_nul(value: str) -> str:
    """Reject NUL: Postgres text cannot store it, and storage runs before insert."""
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


#: MIME types an upload may claim (ADR 0022). The service enforces this set so
#: a violation returns `document_type_not_allowed`, not `validation_error`.
ALLOWED_DOCUMENT_CONTENT_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg"}
)


class DocumentCategory(StrEnum):
    """Pinned document category (`contract|identification|certification|other`)."""

    CONTRACT = "contract"
    IDENTIFICATION = "identification"
    CERTIFICATION = "certification"
    OTHER = "other"


class DocumentUploadCreate(BaseModel):
    """Step 1: the claimed MIME type and size for a presigned upload."""

    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(
        min_length=1,
        max_length=255,
        # Advertise the allowlist in OpenAPI; Pydantic stays permissive so the
        # service can return the pinned `document_type_not_allowed` code.
        json_schema_extra=cast(
            dict[str, Any], {"enum": sorted(ALLOWED_DOCUMENT_CONTENT_TYPES)}
        ),
        description=(
            "Claimed MIME type; must be one of `application/pdf`, `image/png`, "
            "`image/jpeg` (the service returns `document_type_not_allowed` "
            "otherwise)."
        ),
    )
    size_bytes: int = Field(
        ge=1,
        # Advertise the cap in OpenAPI; the service enforces it so violations
        # return `document_too_large`, not the generic `validation_error`.
        json_schema_extra={"maximum": MAX_DOCUMENT_SIZE_BYTES},
        description=(
            "Claimed size in bytes (`1..20971520`). The 20 MB cap is enforced "
            "by the service so violations return `document_too_large`."
        ),
    )


class DocumentUpload(BaseModel):
    """Step 1 response: a presigned POST pinned to the staging key."""

    upload_id: UUID = Field(
        description="Opaque id of the staged upload; pass it back to confirm."
    )
    url: str = Field(
        description="Presigned POST target; a credential — never log or cache it."
    )
    fields: dict[str, str] = Field(
        description=(
            "Signed form fields to send verbatim with the file and the "
            "client-supplied `x-amz-meta-filename` field."
        )
    )
    expires_at: datetime


class DocumentCreate(BaseModel):
    """Step 3: confirm the staged object and create the metadata row."""

    model_config = ConfigDict(extra="forbid")

    upload_id: UUID = Field(
        description=(
            "The staging upload id from `createDocumentUpload`; the object "
            "must still exist at `staging/{tenant_id}/{upload_id}`."
        )
    )
    title: str = Field(min_length=1, max_length=200)
    category: DocumentCategory
    expiry_date: date | None = Field(
        default=None,
        description="Optional `YYYY-MM-DD`; display-only in v1.",
    )

    @field_validator("title")
    @classmethod
    def _title_has_no_nul(cls, value: str) -> str:
        return _require_no_nul(value)


class Document(BaseModel):
    """One document's metadata (pinned response shape).

    Deleted rows never serialize: there is no `deleted_at` field, and the
    service returns `404 document_not_found` instead.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    # PII — never logged, never emitted, never written to audit values.
    title: str
    category: DocumentCategory
    expiry_date: date | None = Field(
        default=None,
        description="Optional `YYYY-MM-DD`; the UI flags past dates as expired.",
    )
    # PII — the original client-supplied name, carried via the staging object's
    # `x-amz-meta-filename` metadata (ADR 0022).
    filename: str = Field(
        max_length=255,
        description=(
            "Original client-supplied filename (percent-encoded UTF-8 in the "
            "staging object's `x-amz-meta-filename`, at most 255 decoded "
            "characters). Never logged or emitted."
        ),
    )
    content_type: str
    size_bytes: int
    uploaded_by: UUID = Field(
        description="Authenticated user id that confirmed the upload (ADR 0010)."
    )
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    """Edit `title`, `category` or `expiry_date`; absent fields are unchanged.

    At least one field is required. Explicit `null` clears `expiry_date` but is
    rejected for `title`/`category`.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: DocumentCategory | None = None
    expiry_date: date | None = None

    @field_validator("title")
    @classmethod
    def _title_has_no_nul(cls, value: str | None) -> str | None:
        return None if value is None else _require_no_nul(value)

    @model_validator(mode="after")
    def _require_one_valid_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in ("title", "category"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class DocumentDownload(BaseModel):
    """A fresh short-lived presigned GET URL (never cached or prefetched)."""

    url: str = Field(
        description="Presigned GET target; a credential — never log or cache it."
    )
    expires_at: datetime


class DocumentListPage(BaseModel):
    """The pinned list envelope (`{"items": [...], "page": Page}`)."""

    items: list[Document]
    page: Page
