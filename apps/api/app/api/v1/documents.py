"""Documents routes: the seven operations pinned in
`.ai/features/employee-documents.md` (`## API contract`).

Thin by rule: identity → service; `now` is taken at this edge only (`_now()` is
the single wall-clock read, every mutating service method takes it as its last
parameter). operationIds are pinned by the feature spec and drive the orval
generated web hooks — rename one here and the web hooks drift.

Authorization lives in `domains/documents/policies.py` and runs inside the
service on every path, so a direct caller cannot bypass the role gate. The
nested route binds a document id to its employee and tenant, which is what
makes a foreign id answer `404` instead of leaking existence.

Upload and download return presigned URLs only — never a storage key and never
a public path (`.ai/security.md`, ADR 0022). Those URLs are bearer credentials:
nothing here logs them.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import DocumentServiceDep, IdentityDep
from app.domains.documents.schemas import (
    Document,
    DocumentCategory,
    DocumentCreate,
    DocumentDownload,
    DocumentListPage,
    DocumentUpdate,
    DocumentUpload,
    DocumentUploadCreate,
)
from app.shared.pagination import PageParamsDep

router = APIRouter(prefix="/employees/{employee_id}/documents")


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.post(
    "/uploads",
    response_model=DocumentUpload,
    operation_id="createDocumentUpload",
    status_code=201,
)
async def create_document_upload(
    employee_id: UUID,
    payload: DocumentUploadCreate,
    identity: IdentityDep,
    service: DocumentServiceDep,
) -> DocumentUpload:
    """Step 1: validate the claim and return a presigned POST for staging."""
    return await service.create_upload(identity, employee_id, payload)


@router.post(
    "",
    response_model=Document,
    operation_id="createDocument",
    status_code=201,
)
async def create_document(
    employee_id: UUID,
    payload: DocumentCreate,
    identity: IdentityDep,
    service: DocumentServiceDep,
) -> Document:
    """Step 3: confirm the staged object → metadata row, audit and event."""
    return await service.create(identity, employee_id, payload, _now())


@router.get("", response_model=DocumentListPage, operation_id="listDocuments")
async def list_documents(
    employee_id: UUID,
    identity: IdentityDep,
    service: DocumentServiceDep,
    page: PageParamsDep,
    category: DocumentCategory | None = Query(default=None),
) -> DocumentListPage:
    """Paged live documents, visibility-scoped first, then category-filtered."""
    return await service.list(identity, employee_id, category=category, page=page)


@router.get("/{document_id}", response_model=Document, operation_id="getDocument")
async def get_document(
    employee_id: UUID,
    document_id: UUID,
    identity: IdentityDep,
    service: DocumentServiceDep,
) -> Document:
    """One live document; unknown, deleted and cross-tenant ids are `404`."""
    return await service.get(identity, employee_id, document_id)


@router.get(
    "/{document_id}/download",
    response_model=DocumentDownload,
    operation_id="downloadDocument",
)
async def download_document(
    employee_id: UUID,
    document_id: UUID,
    identity: IdentityDep,
    service: DocumentServiceDep,
) -> DocumentDownload:
    """Mint a fresh short-lived presigned GET for every authorized call."""
    return await service.download(identity, employee_id, document_id)


@router.patch("/{document_id}", response_model=Document, operation_id="updateDocument")
async def update_document(
    employee_id: UUID,
    document_id: UUID,
    payload: DocumentUpdate,
    identity: IdentityDep,
    service: DocumentServiceDep,
) -> Document:
    """Edit `title`/`category`/`expiry_date`; audited, never evented."""
    return await service.update(identity, employee_id, document_id, payload, _now())


@router.delete("/{document_id}", operation_id="deleteDocument", status_code=204)
async def delete_document(
    employee_id: UUID,
    document_id: UUID,
    identity: IdentityDep,
    service: DocumentServiceDep,
) -> None:
    """Soft-delete the row and purge the object; a second delete is `404`."""
    await service.delete(identity, employee_id, document_id, _now())
