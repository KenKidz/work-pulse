"""In-memory fakes for the Documents domain unit tests (T4).

No I/O anywhere (`.ai/testing.md` — these tests pass with every container
stopped). `FakeDocumentRepo` honors tenant/employee scope, the soft-delete
visibility rule and the conditional update/delete shape of the Postgres
adapter. `FakeDocumentStorage` mirrors the `DocumentStorage` port: presigned
uploads pinned to the staging key, HEAD metadata, server-side copy, idempotent
delete and a fresh presigned GET per call (`failure` hooks let a test abort any
storage step).

`FakeEmployeeScope` is both the manager scope (self + direct reports, one
level, the `PostgresEmployeeScope` rule) and the in-tenant existence reader
(`EmployeeReferenceReader`) so one directory backs both service dependencies.

`journal` is an optional shared list: when the repo and storage receive the
same list, a test can pin cross-port ordering (confirm copies before the row;
delete purges before the soft-delete).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID

from app.domains.documents.repository import (
    DocumentDeleteResult,
    DocumentFilters,
)
from app.domains.documents.schemas import Document, DocumentCategory
from app.domains.documents.storage import (
    ObjectStat,
    PresignedDownload,
    PresignedUpload,
    staging_key,
)
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
SELF = UUID("00000000-0000-4000-8000-000000000002")
REPORT = UUID("00000000-0000-4000-8000-000000000003")
PEER = UUID("00000000-0000-4000-8000-000000000004")
OTHER = UUID("00000000-0000-4000-8000-000000000005")
UPLOAD_ID = UUID("00000000-0000-4000-8000-000000000006")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000007")
DOCUMENT_ID_2 = UUID("00000000-0000-4000-8000-000000000008")

# NOW = 2026-10-26 12:00 UTC — the pinned clock for every mutation test.
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def identity(
    role: Role = Role.HR,
    *,
    tenant_id: UUID = TENANT_A,
    employee_id: UUID | None = SELF,
    actor_id: UUID = USER,
) -> Identity:
    """The acting user (`actor_id`) plus its optional employee link."""
    return Identity(
        tenant_id=tenant_id,
        actor_id=actor_id,
        role=role,
        employee_id=employee_id,
    )


class FakeDocumentRepo:
    """Tenant/employee-scoped in-memory implementation of `DocumentRepository`."""

    def __init__(self, *, journal: list[str] | None = None) -> None:
        # (tenant, employee, document) → wire row; the wire model has no tenant.
        self.rows: dict[tuple[UUID, UUID, UUID], Document] = {}
        self.deleted: set[tuple[UUID, UUID, UUID]] = set()
        self.writes: list[str] = []
        self._journal = journal

    def _write(self, call: str) -> None:
        self.writes.append(call)
        if self._journal is not None:
            self._journal.append(call)

    async def create(
        self,
        tenant_id: UUID,
        document_id: UUID,
        *,
        employee_id: UUID,
        title: str,
        category: DocumentCategory,
        expiry_date: date | None,
        filename: str,
        content_type: str,
        size_bytes: int,
        uploaded_by: UUID,
        now: datetime,
    ) -> Document:
        self._write("repo.create")
        row = Document(
            id=document_id,
            employee_id=employee_id,
            title=title,
            category=category,
            expiry_date=expiry_date,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
            created_at=now,
            updated_at=now,
        )
        self.rows[(tenant_id, employee_id, document_id)] = row
        return row

    async def get(
        self, tenant_id: UUID, employee_id: UUID, document_id: UUID
    ) -> Document | None:
        key = (tenant_id, employee_id, document_id)
        if key in self.deleted:
            return None
        return self.rows.get(key)

    async def list(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        filters: DocumentFilters,
        page: PageParams,
    ) -> tuple[Sequence[Document], int]:
        rows = [
            row
            for (row_tenant, row_employee, _), row in self.rows.items()
            if row_tenant == tenant_id
            and row_employee == employee_id
            and (row_tenant, row_employee, row.id) not in self.deleted
            and (filters.category is None or row.category is filters.category)
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def update(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Document | None:
        self._write("repo.update")
        key = (tenant_id, employee_id, document_id)
        row = self.rows.get(key)
        if row is None or key in self.deleted:
            return None
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.rows[key] = updated
        return updated

    async def soft_delete(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
    ) -> DocumentDeleteResult | None:
        self._write("repo.soft_delete")
        key = (tenant_id, employee_id, document_id)
        row = self.rows.get(key)
        if row is None:
            return None
        if key in self.deleted:
            return DocumentDeleteResult(document=row, changed=False)
        self.deleted.add(key)
        updated = row.model_copy(update={"updated_at": now})
        self.rows[key] = updated
        return DocumentDeleteResult(document=updated, changed=True)


class FakeDocumentStorage:
    """In-memory `DocumentStorage` with failure hooks and a call journal."""

    def __init__(self, *, journal: list[str] | None = None) -> None:
        self.objects: dict[str, ObjectStat] = {}
        self.uploads: list[tuple[UUID, UUID, str, int]] = []
        self.copies: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.downloads: list[str] = []
        self.fail_copy = False
        self.copy_error: Exception | None = None
        self.fail_delete = False
        self._download_serial = 0
        self._journal = journal

    def _record(self, call: str) -> None:
        if self._journal is not None:
            self._journal.append(call)

    async def create_upload(
        self,
        tenant_id: UUID,
        upload_id: UUID,
        *,
        content_type: str,
        size_bytes: int,
        ttl: timedelta | None = None,
    ) -> PresignedUpload:
        self._record("storage.create_upload")
        self.uploads.append((tenant_id, upload_id, content_type, size_bytes))
        key = staging_key(tenant_id, upload_id)
        return PresignedUpload(
            url=f"https://minio.test/{key}",
            fields={
                "key": key,
                "Content-Type": content_type,
                "x-amz-meta-claimed-size": str(size_bytes),
            },
            expires_at=NOW + (ttl or timedelta(minutes=10)),
        )

    async def stat(self, key: str) -> ObjectStat | None:
        return self.objects.get(key)

    async def copy(self, source_key: str, destination_key: str) -> None:
        self._record("storage.copy")
        if self.copy_error is not None:
            raise self.copy_error
        if self.fail_copy:
            raise RuntimeError("copy failed")
        stat = self.objects.get(source_key)
        if stat is None:
            raise FileNotFoundError(source_key)
        self.copies.append((source_key, destination_key))
        self.objects[destination_key] = stat

    async def delete(self, key: str) -> None:
        self._record("storage.delete")
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def presign_download(
        self, key: str, ttl: timedelta | None = None
    ) -> PresignedDownload:
        self._record("storage.presign_download")
        self._download_serial += 1
        self.downloads.append(key)
        return PresignedDownload(
            url=f"https://minio.test/{key}?sig={self._download_serial}",
            expires_at=NOW + (ttl or timedelta(minutes=5)),
        )


class FakeEmployeeScope:
    """Manager scope and in-tenant existence over one employee directory.

    Keys are `(tenant_id, employee_id)`, values the manager link (`None` for a
    root employee). `visible_employee_ids` applies the shared one-level rule:
    self OR `manager_id = manager`.
    """

    def __init__(self) -> None:
        self.employees: dict[tuple[UUID, UUID], UUID | None] = {}

    def add(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        manager_id: UUID | None = None,
    ) -> None:
        self.employees[(tenant_id, employee_id)] = manager_id

    async def exists(self, tenant_id: UUID, employee_id: UUID) -> bool:
        return (tenant_id, employee_id) in self.employees

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> Sequence[UUID]:
        if manager_employee_id is None:
            return ()
        return tuple(
            sorted(
                employee_id
                for (row_tenant, employee_id), manager_id in self.employees.items()
                if row_tenant == tenant_id
                and (
                    employee_id == manager_employee_id
                    or manager_id == manager_employee_id
                )
            )
        )


def staged_metadata(
    *,
    filename: str = "contract.pdf",
    size_bytes: int = 1024,
    claimed_size: int | None = None,
) -> dict[str, str]:
    """The protocol metadata a staged object carries (percent-encoded filename).

    Tests that need a broken protocol build the mapping directly instead.
    """
    return {
        "filename": quote(filename, safe=""),
        "claimed-size": str(size_bytes if claimed_size is None else claimed_size),
    }


def object_stat(
    *,
    size_bytes: int = 1024,
    content_type: str = "application/pdf",
    metadata: Mapping[str, str] | None = None,
) -> ObjectStat:
    """A staged-object HEAD result with the default protocol metadata."""
    return ObjectStat(
        size_bytes=size_bytes,
        content_type=content_type,
        metadata=dict(metadata if metadata is not None else staged_metadata()),
    )
