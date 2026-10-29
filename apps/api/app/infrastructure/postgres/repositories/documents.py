"""Postgres repository for the Documents domain (T3).

Every statement carries `WHERE tenant_id = :tenant` first and binds the nested
`employee_id`, so a document id cannot be reached through another employee's
path (`.ai/security.md`). Visible reads always add `deleted_at IS NULL`;
soft-deleted rows exist only for traceability and never serialize.

`soft_delete` is a conditional update on `deleted_at IS NULL` so two concurrent
deletes cannot both claim the transition: the winner returns `changed=True`,
the loser sees the row still present and returns `changed=False` (the service
maps that to the pinned second-delete `404`). `update` is conditional the same
way, so a deleted row is never edited.

Reads return the pinned wire shape (`schemas.py`); ORM rows never leave this
module. `populate_existing` keeps a row read after a core UPDATE from serving a
stale identity-mapped instance within one request.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.documents import models as orm
from app.domains.documents.repository import (
    DocumentDeleteResult,
    DocumentFilters,
)
from app.domains.documents.schemas import Document, DocumentCategory
from app.shared.pagination import PageParams


def _document(row: orm.Document) -> Document:
    """ORM row → the pinned wire shape."""
    return Document.model_validate(row)


class PostgresDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _row(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        document_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> orm.Document | None:
        """One tenant/employee-scoped row, live unless `include_deleted`."""
        conditions = [
            orm.Document.tenant_id == tenant_id,
            orm.Document.employee_id == employee_id,
            orm.Document.id == document_id,
        ]
        if not include_deleted:
            conditions.append(orm.Document.deleted_at.is_(None))
        stmt = (
            select(orm.Document)
            .where(*conditions)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

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
        row = orm.Document(
            id=document_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            title=title,
            category=category.value,
            expiry_date=expiry_date,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _document(row)

    async def get(
        self, tenant_id: UUID, employee_id: UUID, document_id: UUID
    ) -> Document | None:
        row = await self._row(tenant_id, employee_id, document_id)
        return _document(row) if row is not None else None

    async def list(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        filters: DocumentFilters,
        page: PageParams,
    ) -> tuple[Sequence[Document], int]:
        conditions = [
            orm.Document.tenant_id == tenant_id,
            orm.Document.employee_id == employee_id,
            orm.Document.deleted_at.is_(None),
        ]
        if filters.category is not None:
            conditions.append(orm.Document.category == filters.category.value)

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.Document).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.Document)
            .where(*conditions)
            .order_by(orm.Document.created_at.desc(), orm.Document.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).scalars()
        return [_document(row) for row in rows], total_items

    async def update(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Document | None:
        stmt = (
            update(orm.Document)
            .where(
                orm.Document.tenant_id == tenant_id,
                orm.Document.employee_id == employee_id,
                orm.Document.id == document_id,
                orm.Document.deleted_at.is_(None),
            )
            .values(**changes, updated_at=now)
            .returning(orm.Document.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        row = await self._row(tenant_id, employee_id, document_id)
        return _document(row) if row is not None else None

    async def soft_delete(
        self, tenant_id: UUID, employee_id: UUID, document_id: UUID, *, now: datetime
    ) -> DocumentDeleteResult | None:
        stmt = (
            update(orm.Document)
            .where(
                orm.Document.tenant_id == tenant_id,
                orm.Document.employee_id == employee_id,
                orm.Document.id == document_id,
                orm.Document.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_at=now)
            .returning(orm.Document.id)
        )
        changed = (await self._session.execute(stmt)).scalar_one_or_none()
        row = await self._row(tenant_id, employee_id, document_id, include_deleted=True)
        if row is None:
            return None
        return DocumentDeleteResult(
            document=_document(row), changed=changed is not None
        )


__all__ = ["PostgresDocumentRepository"]
