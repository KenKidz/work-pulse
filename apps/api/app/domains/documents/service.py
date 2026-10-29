"""Use cases for the Documents domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/employee-documents.md`):

- **Two-step upload**: `create_upload` validates the claim (MIME allowlist,
  20 MB cap) and presigns a POST pinned to
  `staging/{tenant_id}/{upload_id}`. `create` (confirm) HEAD-checks the staged
  object, re-checks type/size against the claim carried in the object
  metadata, copies it to `documents/{tenant_id}/{employee_id}/{document_id}`,
  purges staging and only then inserts the metadata row, audit entry and event
  in one transaction. An abandoned upload creates no row, no audit entry and
  no event.
- **Writes are HR/Admin/Super Admin only** (`ensure_can_manage`). **Reads** are
  the owner (any role), a manager over self + direct reports (`EmployeeScope`,
  one level), HR/Admin/Super Admin everyone and Finance their own only
  (`ensure_can_read`). A same-tenant out-of-scope read is `403 forbidden`; a
  foreign, unknown or deleted nested id is `404`.
- **Metadata updates are field updates**: audited as `document.updated`, no
  event (the `progress_pct` precedent). Delete is soft-delete plus object
  purge: the object goes first (a storage failure leaves the row and audit
  trail intact and the call retryable), then `deleted_at` + audit +
  `document.deleted` in one transaction.
- **No expiry behavior**: `expiry_date` is stored and returned only.
- **PII** (`title`, `filename`, storage keys, presigned URLs) is never logged,
  emitted or written into audit `changes` values. Keys are derived from ids,
  never stored, so a presigned URL never carries a filename.
- **Clock**: every mutation takes `now` as its last parameter — the router is
  the only wall-clock reader.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote_to_bytes
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.documents import events
from app.domains.documents.exceptions import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    DocumentTypeNotAllowedError,
    DocumentUploadNotCompletedError,
    EmployeeNotFoundError,
)
from app.domains.documents.policies import ensure_can_manage, ensure_can_read
from app.domains.documents.repository import (
    DocumentFilters,
    DocumentRepository,
    EmployeeReferenceReader,
    EmployeeScope,
)
from app.domains.documents.schemas import (
    ALLOWED_DOCUMENT_CONTENT_TYPES,
    MAX_DOCUMENT_SIZE_BYTES,
    Document,
    DocumentCategory,
    DocumentCreate,
    DocumentDownload,
    DocumentListPage,
    DocumentUpdate,
    DocumentUpload,
    DocumentUploadCreate,
)
from app.domains.documents.storage import (
    DocumentStorage,
    ObjectMissingError,
    document_key,
    staging_key,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity, Role
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent
_MAX_FILENAME_LENGTH = 255


def _value(value: object) -> object:
    """Audit-safe JSON value: ids/dates/enums become strings, never objects."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, DocumentCategory):
        return value.value
    return value


def _claimed_size(metadata: Mapping[str, str]) -> int:
    """The intent's declared size, carried in the staging object metadata.

    Missing or malformed metadata means the pinned upload protocol was not
    completed (`409`), never a generic validation error: it is a server-side
    protocol invariant, not client input (ADR 0022).
    """
    raw = metadata.get("claimed-size")
    if raw is None:
        raise DocumentUploadNotCompletedError()
    try:
        claimed = int(raw)
    except ValueError:
        raise DocumentUploadNotCompletedError() from None
    if claimed < 1:
        raise DocumentUploadNotCompletedError()
    return claimed


def _filename(metadata: Mapping[str, str]) -> str:
    """Decode the percent-encoded UTF-8 filename channel (ADR 0022)."""
    raw = metadata.get("filename")
    if not raw:
        raise DocumentUploadNotCompletedError()
    try:
        filename = unquote_to_bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentUploadNotCompletedError() from None
    if not 1 <= len(filename) <= _MAX_FILENAME_LENGTH:
        raise DocumentUploadNotCompletedError()
    if "\x00" in filename:
        # Postgres text cannot store NUL; the protocol metadata is unusable and
        # this must fail before the copy/purge move (the row would 500 after it).
        raise DocumentUploadNotCompletedError()
    return filename


class DocumentService:
    """Document use cases over the repository, storage and audit/outbox ports."""

    def __init__(
        self,
        repo: DocumentRepository,
        storage: DocumentStorage,
        employees: EmployeeReferenceReader,
        scope: EmployeeScope,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._employees = employees
        self._scope = scope
        self._audit = audit
        self._outbox = outbox

    # --- mutations -----------------------------------------------------------

    async def create_upload(
        self, identity: Identity, employee_id: UUID, payload: DocumentUploadCreate
    ) -> DocumentUpload:
        """Step 1: validate the claim and presign a POST for the staging key."""
        ensure_can_manage(identity)
        await self._ensure_employee(identity.tenant_id, employee_id)
        if payload.content_type not in ALLOWED_DOCUMENT_CONTENT_TYPES:
            raise DocumentTypeNotAllowedError()
        if payload.size_bytes < 1 or payload.size_bytes > MAX_DOCUMENT_SIZE_BYTES:
            raise DocumentTooLargeError()

        upload_id = uuid7()
        presigned = await self._storage.create_upload(
            identity.tenant_id,
            upload_id,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
        )
        return DocumentUpload(
            upload_id=upload_id,
            url=presigned.url,
            fields=presigned.fields,
            expires_at=presigned.expires_at,
        )

    async def create(
        self,
        identity: Identity,
        employee_id: UUID,
        payload: DocumentCreate,
        now: datetime,
    ) -> Document:
        """Step 3: confirm the staged object, then row + audit + event.

        Storage runs before the row (ADR 0022): a transaction failure after the
        copy leaves an orphan final object, never a row pointing at no bytes.
        The checks run before any write, so an incomplete upload writes nothing.
        A staging object that vanishes between HEAD and copy (a concurrent
        confirm won the purge race) maps to the pinned 409 via
        `ObjectMissingError`, never a 500.
        """
        ensure_can_manage(identity)
        await self._ensure_employee(identity.tenant_id, employee_id)

        staged = staging_key(identity.tenant_id, payload.upload_id)
        stat = await self._storage.stat(staged)
        if stat is None:
            raise DocumentUploadNotCompletedError()
        if stat.content_type not in ALLOWED_DOCUMENT_CONTENT_TYPES:
            raise DocumentTypeNotAllowedError()
        claimed = _claimed_size(stat.metadata)
        if stat.size_bytes < 1:
            # Nothing usable was staged; the same pinned code as a missing
            # object (the storage policy refuses an empty body first).
            raise DocumentUploadNotCompletedError()
        if stat.size_bytes > MAX_DOCUMENT_SIZE_BYTES or stat.size_bytes > claimed:
            raise DocumentTooLargeError()
        filename = _filename(stat.metadata)

        document_id = uuid7()
        final = document_key(identity.tenant_id, employee_id, document_id)
        try:
            await self._storage.copy(staged, final)
        except ObjectMissingError:
            # A concurrent confirm purged staging after our HEAD: this caller
            # is the loser and gets the pinned 409, never a 500 (ADR 0022).
            raise DocumentUploadNotCompletedError() from None
        await self._storage.delete(staged)

        document = await self._repo.create(
            identity.tenant_id,
            document_id,
            employee_id=employee_id,
            title=payload.title,
            category=payload.category,
            expiry_date=payload.expiry_date,
            filename=filename,
            content_type=stat.content_type,
            size_bytes=stat.size_bytes,
            uploaded_by=identity.actor_id,
            now=now,
        )
        await self._outbox.append(
            events.document_uploaded(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                document_id=document.id,
                employee_id=employee_id,
                category=document.category,
            )
        )
        changes: dict[str, Any] = {
            "employee_id": {"old": None, "new": str(employee_id)},
            "category": {"old": None, "new": document.category.value},
            "content_type": {"old": None, "new": document.content_type},
            "size_bytes": {"old": None, "new": document.size_bytes},
            "title": PII_CHANGED,  # PII: never the value
            "filename": PII_CHANGED,  # PII: never the value
        }
        if document.expiry_date is not None:
            changes["expiry_date"] = {
                "old": None,
                "new": document.expiry_date.isoformat(),
            }
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="document.uploaded",
                entity_type="document",
                entity_id=document.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return document

    async def update(
        self,
        identity: Identity,
        employee_id: UUID,
        document_id: UUID,
        payload: DocumentUpdate,
        now: datetime,
    ) -> Document:
        """Edit `title`/`category`/`expiry_date`; audited, never evented."""
        ensure_can_manage(identity)
        await self._ensure_employee(identity.tenant_id, employee_id)
        existing = await self._repo.get(identity.tenant_id, employee_id, document_id)
        if existing is None:
            raise DocumentNotFoundError()

        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            # The schema rejects an empty PATCH; this only guards a direct call.
            return existing

        updated = await self._repo.update(
            identity.tenant_id, employee_id, document_id, now=now, **changes
        )
        if updated is None:
            # Deleted between the read and the conditional write: never edit a
            # dead row.
            raise DocumentNotFoundError()

        audit_changes: dict[str, Any] = {}
        for field in changes:
            if field == "title":
                audit_changes[field] = PII_CHANGED  # PII: never the value
            else:
                audit_changes[field] = {
                    "old": _value(getattr(existing, field)),
                    "new": _value(getattr(updated, field)),
                }
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="document.updated",
                entity_type="document",
                entity_id=document_id,
                occurred_at=now,
                changes=audit_changes,
            )
        )
        return updated

    async def delete(
        self, identity: Identity, employee_id: UUID, document_id: UUID, now: datetime
    ) -> None:
        """Soft-delete the row and purge the object; the purge goes first.

        Purge-first (ADR 0022): a storage failure aborts before any DB write, so
        the row, audit trail and object stay consistent and the call is
        retryable. A missing object is already gone and tolerated. A second
        delete is the pinned `404 document_not_found`, deliberately unlike the
        `archived_at` idempotent no-op.
        """
        ensure_can_manage(identity)
        await self._ensure_employee(identity.tenant_id, employee_id)
        existing = await self._repo.get(identity.tenant_id, employee_id, document_id)
        if existing is None:
            raise DocumentNotFoundError()

        await self._storage.delete(
            document_key(identity.tenant_id, employee_id, document_id)
        )
        result = await self._repo.soft_delete(
            identity.tenant_id, employee_id, document_id, now=now
        )
        if result is None or not result.changed:
            # A concurrent delete won the conditional update; the loser sees
            # the same pinned 404 as any already-deleted row.
            raise DocumentNotFoundError()

        await self._outbox.append(
            events.document_deleted(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                document_id=document_id,
                employee_id=employee_id,
                category=result.document.category,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="document.deleted",
                entity_type="document",
                entity_id=document_id,
                occurred_at=now,
                changes={"deleted_at": {"old": None, "new": now.isoformat()}},
            )
        )

    # --- reads ---------------------------------------------------------------

    async def list(
        self,
        identity: Identity,
        employee_id: UUID,
        *,
        category: DocumentCategory | None = None,
        page: PageParams,
    ) -> DocumentListPage:
        """Paged live documents for one employee, visibility-scoped first."""
        await self._ensure_readable(identity, employee_id)
        items, total = await self._repo.list(
            identity.tenant_id,
            employee_id,
            DocumentFilters(category=category),
            page,
        )
        return DocumentListPage(items=list(items), page=page.make_page(total))

    async def get(
        self, identity: Identity, employee_id: UUID, document_id: UUID
    ) -> Document:
        """One live document; unknown, deleted and cross-tenant ids are `404`."""
        await self._ensure_readable(identity, employee_id)
        document = await self._repo.get(identity.tenant_id, employee_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        return document

    async def download(
        self, identity: Identity, employee_id: UUID, document_id: UUID
    ) -> DocumentDownload:
        """Mint a **fresh** short-lived GET on every call; never cached."""
        document = await self.get(identity, employee_id, document_id)
        presigned = await self._storage.presign_download(
            document_key(identity.tenant_id, employee_id, document.id)
        )
        return DocumentDownload(url=presigned.url, expires_at=presigned.expires_at)

    # --- internals -----------------------------------------------------------

    async def _ensure_employee(self, tenant_id: UUID, employee_id: UUID) -> None:
        """In-tenant existence; a foreign or unknown id is `404`."""
        if not await self._employees.exists(tenant_id, employee_id):
            raise EmployeeNotFoundError()

    async def _ensure_readable(self, identity: Identity, employee_id: UUID) -> None:
        """`404` for a foreign/unknown employee, then `403` when out of scope.

        Existence is checked first so a cross-tenant id never leaks as `403`
        and the manager scope is resolved only for a manager (the policy
        ignores it for every other role).
        """
        await self._ensure_employee(identity.tenant_id, employee_id)
        visible: Sequence[UUID] = ()
        if identity.role is Role.MANAGER:
            visible = await self._scope.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
        ensure_can_read(identity, employee_id, visible)
