"""Read-only display-label lookup for report group keys (T5, ADR 0019).

Report rows carry identifiers, never names (`.ai/security.md`). The service
resolves a group key through this adapter at query time; the adapter reads the
Organizations/Leave reference tables through their ORM models (the documented
infrastructure seam, `PostgresRecipientDirectory` precedent) and never writes
them. Every query carries `tenant_id` first, so a cross-tenant key can never
resolve to a name.

Unknown keys are omitted from the mapping; the service renders `Unknown`.
"""

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.leave.models import LeaveType
from app.domains.organizations.models import Department, Location, Position
from app.domains.reports.schemas import UNKNOWN_GROUP_KEY


class PostgresReferenceLabels:
    """`ReferenceLabels` over the Organizations and Leave reference tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def labels(
        self, tenant_id: UUID, dimension: str, keys: Sequence[str]
    ) -> Mapping[str, str]:
        if dimension == "leave_type":
            return await self._leave_type_labels(tenant_id, keys)

        identifiers = _uuid_keys(keys)
        if not identifiers:
            return {}

        if dimension == "department":
            stmt = select(Department.id, Department.name).where(
                Department.tenant_id == tenant_id,
                Department.id.in_(identifiers),
            )
        elif dimension == "location":
            stmt = select(Location.id, Location.name).where(
                Location.tenant_id == tenant_id,
                Location.id.in_(identifiers),
            )
        elif dimension == "position":
            stmt = select(Position.id, Position.title).where(
                Position.tenant_id == tenant_id,
                Position.id.in_(identifiers),
            )
        else:  # pragma: no cover — the service only asks for known dimensions
            return {}

        rows = (await self._session.execute(stmt)).all()
        return {str(row.id): row[1] for row in rows}

    async def _leave_type_labels(
        self, tenant_id: UUID, keys: Sequence[str]
    ) -> Mapping[str, str]:
        codes = [key for key in keys if key and key != UNKNOWN_GROUP_KEY]
        if not codes:
            return {}
        stmt = select(LeaveType.code, LeaveType.name).where(
            LeaveType.tenant_id == tenant_id,
            LeaveType.code.in_(codes),
        )
        rows = (await self._session.execute(stmt)).all()
        return {row.code: row[1] for row in rows}


def _uuid_keys(keys: Sequence[str]) -> list[UUID]:
    """Parse dimension keys defensively; a malformed key simply never resolves."""
    identifiers: list[UUID] = []
    for key in keys:
        if not key or key == UNKNOWN_GROUP_KEY:
            continue
        try:
            identifiers.append(UUID(key))
        except ValueError:
            continue
    return identifiers


__all__ = ["PostgresReferenceLabels"]
