"""Postgres repositories for the Identity domain.

Tenant scoping lives here and nowhere else (`.ai/security.md`): every
tenant-scoped statement carries `WHERE tenant_id = :tenant`. The two global
lookups (email, token hash) are the documented exceptions — the account/token
resolves the tenant. Unique-violation `IntegrityError`s are mapped to the
domain `*_taken` errors (the DB constraint is the race backstop behind the
service pre-check); constraint names are the explicit ones declared in
`domains/identity/models.py`.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity import models as orm
from app.domains.identity.exceptions import UserEmailTakenError
from app.domains.identity.policies import ADMIN_ROLES
from app.domains.identity.repository import UserFilters
from app.domains.identity.schemas import Session, User, UserCredentials
from app.infrastructure.postgres.like import LIKE_ESCAPE, contains_pattern
from app.shared.errors import WorkPulseError
from app.shared.identity import Role
from app.shared.pagination import PageParams


def _credentials(row: orm.User) -> UserCredentials:
    """The internal credential projection — wire `User` + password hash."""
    return UserCredentials(
        user=User.model_validate(row), password_hash=row.password_hash
    )


def _map_unique_violation(exc: IntegrityError) -> WorkPulseError | None:
    """Map a unique-constraint race to the pinned domain error.

    Asyncpg exposes the violated constraint/index name; the message fallback
    keeps the mapping working for wrapped drivers. Anything else (e.g. a
    foreign-key violation) is not ours to reinterpret — the caller re-raises it
    unchanged.
    """
    haystack = getattr(exc.orig, "constraint_name", None) or str(exc.orig)
    if "uq_users_email" in haystack:
        return UserEmailTakenError()
    return None


class PostgresUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID, user_id: UUID) -> User | None:
        stmt = select(orm.User).where(
            orm.User.id == user_id,
            orm.User.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return User.model_validate(row) if row is not None else None

    async def get_by_email(self, email: str) -> UserCredentials | None:
        # Global on purpose: login resolves the tenant from the account.
        stmt = select(orm.User).where(orm.User.email == email)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _credentials(row) if row is not None else None

    async def get_credentials(
        self, tenant_id: UUID, user_id: UUID
    ) -> UserCredentials | None:
        stmt = select(orm.User).where(
            orm.User.id == user_id,
            orm.User.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _credentials(row) if row is not None else None

    async def email_taken(self, email: str, exclude_id: UUID | None = None) -> bool:
        # Global on purpose (decision 7): one account per email across tenants.
        stmt = select(orm.User.id).where(orm.User.email == email)
        if exclude_id is not None:
            stmt = stmt.where(orm.User.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def employee_link_taken(
        self, tenant_id: UUID, employee_id: UUID, exclude_id: UUID | None = None
    ) -> bool:
        stmt = select(orm.User.id).where(
            orm.User.tenant_id == tenant_id,
            orm.User.employee_id == employee_id,
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.User.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def count_active_admins(self, tenant_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(orm.User)
            .where(
                orm.User.tenant_id == tenant_id,
                orm.User.active.is_(True),
                orm.User.role.in_(sorted(role.value for role in ADMIN_ROLES)),
            )
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def list(
        self, tenant_id: UUID, filters: UserFilters, page: PageParams
    ) -> tuple[Sequence[User], int]:
        conditions = [orm.User.tenant_id == tenant_id]
        if filters.q:
            conditions.append(
                orm.User.email.ilike(contains_pattern(filters.q), escape=LIKE_ESCAPE)
            )
        if filters.role is not None:
            conditions.append(orm.User.role == filters.role.value)
        if filters.active is not None:
            conditions.append(orm.User.active.is_(filters.active))

        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.User).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(orm.User)
                .where(*conditions)
                # Pinned default order: email ascending (globally unique, so
                # pages are stable without a tiebreaker).
                .order_by(orm.User.email.asc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return [User.model_validate(row) for row in rows], total

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
    ) -> User:
        row = orm.User(
            id=user_id,
            tenant_id=tenant_id,
            email=email,
            role=role.value,
            employee_id=employee_id,
            password_hash=password_hash,
            active=active,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return User.model_validate(row)

    async def update(
        self, tenant_id: UUID, user_id: UUID, **changes: Any
    ) -> User | None:
        stmt = select(orm.User).where(
            orm.User.id == user_id,
            orm.User.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # email race after the service pre-check
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return User.model_validate(row)


class PostgresSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        tenant_id: UUID,
        session_id: UUID,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> Session:
        row = orm.Session(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return Session.model_validate(row)

    async def get_by_token_hash(self, token_hash: str, now: datetime) -> Session | None:
        # Global on purpose: the token hash is the lookup key and the session
        # row carries the tenant. Expired rows are rejected on read.
        stmt = select(orm.Session).where(
            orm.Session.token_hash == token_hash,
            orm.Session.expires_at > now,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Session.model_validate(row) if row is not None else None

    async def delete(self, token_hash: str) -> None:
        await self._session.execute(
            delete(orm.Session).where(orm.Session.token_hash == token_hash)
        )

    async def delete_for_user(self, tenant_id: UUID, user_id: UUID) -> int:
        result = await self._session.execute(
            delete(orm.Session)
            .where(
                orm.Session.tenant_id == tenant_id,
                orm.Session.user_id == user_id,
            )
            .returning(orm.Session.id)
        )
        return len(result.scalars().all())
