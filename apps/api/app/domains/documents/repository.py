"""Data-access abstractions for the Documents domain (T3).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` and `employee_id` explicitly: tenant scoping is enforced in
repository implementations (`WHERE tenant_id = :tenant`), never in routers or
services, and the nested route keeps a document id bound to its employee so a
row cannot be reached through another employee's path.

Visible reads always filter `deleted_at IS NULL`; soft-deleted rows exist only
for traceability and never serialize over HTTP. `soft_delete` distinguishes
"already deleted" (`changed=False`) from "missing or foreign" (`None`) so the
service can map both to the pinned `404 document_not_found` without a second
read.

`EmployeeScope` is the same self + direct-reports port leave/overtime/
performance/reports use; the shared `PostgresEmployeeScope` adapter is reused
so a manager's document visibility cannot drift from their inbox scope.
`EmployeeReferenceReader` is the in-tenant employee-existence read used for
`404 employee_not_found`; `PostgresEmployeeLink` already implements it, so the
documents repository never touches the Employees tables (architecture rule 3).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.documents.schemas import Document, DocumentCategory
from app.shared.pagination import PageParams


class EmployeeScope(Protocol):
    """Read-only manager scope: self + direct reports (one level)."""

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> Sequence[UUID]:
        """The manager's visible employee ids; `[]` for an unlinked manager."""
        ...


class EmployeeReferenceReader(Protocol):
    """In-tenant employee existence check (a cross-context read port).

    Same-tenant only — a cross-tenant id answers exactly like a missing one,
    which is what produces the pinned `404 employee_not_found`.
    """

    async def exists(self, tenant_id: UUID, employee_id: UUID) -> bool: ...


class DocumentFilters(BaseModel):
    """Effective list filters (visibility is resolved before the query)."""

    category: DocumentCategory | None = None


@dataclass(frozen=True, slots=True)
class DocumentDeleteResult:
    """Outcome of a soft-delete call.

    `changed=False` means the row was already soft-deleted; the service maps it
    to the pinned `404 document_not_found` (the spec's second-delete rule,
    deliberately unlike the `archived_at` no-op). `None` from the repository
    means the row is missing or foreign.
    """

    document: Document
    changed: bool


class DocumentRepository(Protocol):
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
        """Insert one metadata row after a confirmed upload."""
        ...

    async def get(
        self, tenant_id: UUID, employee_id: UUID, document_id: UUID
    ) -> Document | None:
        """Tenant/employee-scoped detail; `None` when missing, foreign or deleted."""
        ...

    async def list(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        filters: DocumentFilters,
        page: PageParams,
    ) -> tuple[Sequence[Document], int]:
        """Paged live documents, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def update(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Document | None:
        """Patch fields on a live row; `None` when missing, foreign or deleted."""
        ...

    async def soft_delete(
        self, tenant_id: UUID, employee_id: UUID, document_id: UUID, *, now: datetime
    ) -> DocumentDeleteResult | None:
        """Set `deleted_at`; already-deleted is `changed=False`, missing is `None`."""
        ...
