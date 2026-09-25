"""Postgres implementation of the `AuditWriter` port — insert-only."""

from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.models_audit import AuditEntry
from app.shared.audit import AuditEntryCreate


class PostgresAuditWriter:
    """Writes audit entries inside the request's session/transaction.

    Append-only by construction: this class exposes no update or delete.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def write(self, entry: AuditEntryCreate) -> None:
        self._session.add(AuditEntry(id=uuid7(), **entry.model_dump()))
        await self._session.flush()
