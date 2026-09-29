"""Data-access abstractions for the Identity domain.

Protocols only — the domain never imports infrastructure. Repositories speak the
wire schemas (`schemas.py`); ORM translation happens inside the Postgres
adapters.

Two lookups are deliberately **global** (documented on the methods):
`UserRepository.get_by_email` / `email_taken` (email is globally unique, and
login resolves the tenant from the account — decision 7) and
`SessionRepository.get_by_token_hash` (the token hash is the secret-derived
lookup key; the resolved session row carries `tenant_id`). Every other method
takes `tenant_id` explicitly and is enforced with `WHERE tenant_id = :tenant`
in the implementations.

`UserRepository.update(**changes)` semantics: only the keys present are written.
Allowed keys: `email`, `role`, `employee_id` (None unlinks), `active`,
`password_hash` (credential mutations; never audited by value).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.identity.schemas import Session, User
from app.shared.identity import Role
from app.shared.pagination import PageParams


class UserFilters(BaseModel):
    """List filters (pinned contract: q + role + active)."""

    q: str | None = None
    role: Role | None = None
    active: bool | None = None


class UserRepository(Protocol):
    async def get(self, tenant_id: UUID, user_id: UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """Global lookup — the account resolves the tenant at login.

        Exact match: `users.email` is unique globally with no case-folding
        normalization (pinned), so this lookup matches the unique constraint
        exactly.
        """
        ...

    async def email_taken(self, email: str, exclude_id: UUID | None = None) -> bool:
        """Global uniqueness (decision 7) — deliberately not tenant-scoped."""
        ...

    async def employee_link_taken(
        self, tenant_id: UUID, employee_id: UUID, exclude_id: UUID | None = None
    ) -> bool: ...

    async def count_active_admins(self, tenant_id: UUID) -> int:
        """Active `admin` + `super_admin` users — the `last_admin` guard."""
        ...

    async def list(
        self, tenant_id: UUID, filters: UserFilters, page: PageParams
    ) -> tuple[Sequence[User], int]: ...

    async def add(
        self,
        tenant_id: UUID,
        user_id: UUID,
        *,
        email: str,
        role: Role,
        employee_id: UUID | None,
        password_hash: str,
        active: bool = True,
    ) -> User: ...

    async def update(
        self, tenant_id: UUID, user_id: UUID, **changes: Any
    ) -> User | None: ...


class SessionRepository(Protocol):
    async def add(
        self,
        tenant_id: UUID,
        session_id: UUID,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> Session: ...

    async def get_by_token_hash(self, token_hash: str, now: datetime) -> Session | None:
        """Global lookup by the secret-derived key; expired rows are rejected."""
        ...

    async def delete(self, token_hash: str) -> None:
        """Logout: delete the session row (idempotent — absent is fine)."""
        ...

    async def delete_for_user(self, tenant_id: UUID, user_id: UUID) -> int:
        """Revoke every session of the user; returns how many were deleted."""
        ...


class EmployeeLinkPort(Protocol):
    """Existence check over `employees`, implemented in infrastructure.

    ADR 0007 precedent: no domain imports another domain's tables. Same-tenant
    only — a cross-tenant id answers exactly like a missing one.
    """

    async def exists(self, tenant_id: UUID, employee_id: UUID) -> bool: ...
