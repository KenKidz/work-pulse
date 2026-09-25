"""Phase 1 dev identity (ADR 0004).

Every request carries `X-Tenant-Id`, `X-Actor-Id` and `X-Role` headers. This
module is the ONE place they are parsed and validated — routers and domains
consume `DevIdentity` exactly as they will consume a real session in Phase 2.

Fail closed: missing/malformed tenant or actor → 400 `invalid_identity`;
unknown role → 403 `forbidden`.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from pydantic import BaseModel

from app.shared.errors import ForbiddenError, InvalidIdentityError


class Role(StrEnum):
    """The six pinned Phase 1 roles (`.ai/security.md`)."""

    EMPLOYEE = "employee"
    MANAGER = "manager"
    HR = "hr"
    FINANCE = "finance"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class DevIdentity(BaseModel):
    """Validated identity of the current request."""

    tenant_id: UUID
    actor_id: UUID
    role: Role


def _parse_uuid(raw: str | None, header: str) -> UUID:
    if raw is None or not raw.strip():
        raise InvalidIdentityError(f"Missing identity header {header}")
    try:
        return UUID(raw.strip())
    except ValueError:
        raise InvalidIdentityError(f"Malformed identity header {header}") from None


def parse_identity(
    tenant_raw: str | None,
    actor_raw: str | None,
    role_raw: str | None,
) -> DevIdentity:
    """Pure parsing of the three dev-identity headers. Fail closed."""
    tenant_id = _parse_uuid(tenant_raw, "X-Tenant-Id")
    actor_id = _parse_uuid(actor_raw, "X-Actor-Id")

    if role_raw is None or not role_raw.strip():
        raise InvalidIdentityError("Missing identity header X-Role")
    try:
        role = Role(role_raw.strip().lower())
    except ValueError:
        # Unknown role: deny (403), never guess a fallback.
        raise ForbiddenError("Unknown role") from None

    return DevIdentity(tenant_id=tenant_id, actor_id=actor_id, role=role)


async def get_identity(
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_actor_id: Annotated[str | None, Header()] = None,
    x_role: Annotated[str | None, Header()] = None,
) -> DevIdentity:
    """FastAPI dependency: validate the dev-identity headers."""
    return parse_identity(x_tenant_id, x_actor_id, x_role)


IdentityDep = Annotated[DevIdentity, Depends(get_identity)]
