"""Append-only audit log (`.ai/security.md`).

Domains build `AuditEntryCreate` values and hand them to an `AuditWriter`
port; the Postgres implementation lands with the repositories. There is no
update or delete path anywhere — the table is insert-only.

PII rule: `changes` holds field names + old/new values for non-PII fields
only; PII changes are recorded as "field changed", never values.
"""

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel


class AuditEntryCreate(BaseModel):
    """One audit entry, exactly the pinned shape — no PII slots."""

    tenant_id: UUID
    actor_id: UUID
    action: str  # e.g. "department.created"
    entity_type: str  # e.g. "department"
    entity_id: UUID
    occurred_at: datetime
    reason: str | None = None
    changes: dict[str, Any] | None = None


class AuditWriter(Protocol):
    """Write-only port. Implementations must be insert-only."""

    async def write(self, entry: AuditEntryCreate) -> None: ...
