"""In-memory fakes for the Identity domain unit tests.

No I/O anywhere (testing.md rule 4 — these tests pass with postgres-test
stopped). Fakes honor tenant scope and the two documented global lookups
(email, token hash), mirror the DB uniqueness backstop, and record every
audit entry.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domains.identity.exceptions import UserEmailTakenError
from app.domains.identity.policies import ADMIN_ROLES
from app.domains.identity.repository import UserFilters
from app.domains.identity.schemas import Session, User, UserCredentials
from app.shared.audit import AuditEntryCreate
from app.shared.identity import Role
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
PLACEHOLDER_HASH = "argon2-placeholder"  # never verifies — for non-login fixtures


class RecordingAuditWriter:
    """Append-only recorder of audit entries."""

    def __init__(self) -> None:
        self.entries: list[AuditEntryCreate] = []

    async def write(self, entry: AuditEntryCreate) -> None:
        self.entries.append(entry)


class FakeUserRepo:
    """Credential rows keyed by `(tenant_id, user_id)` — as the DB stores them."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], UserCredentials] = {}

    async def get(self, tenant_id: UUID, user_id: UUID) -> User | None:
        row = self.rows.get((tenant_id, user_id))
        return row.user if row is not None else None

    async def get_by_email(self, email: str) -> UserCredentials | None:
        # Global on purpose: login resolves the tenant from the account.
        return next(
            (row for row in self.rows.values() if row.user.email == email), None
        )

    async def get_credentials(
        self, tenant_id: UUID, user_id: UUID
    ) -> UserCredentials | None:
        return self.rows.get((tenant_id, user_id))

    async def email_taken(self, email: str, exclude_id: UUID | None = None) -> bool:
        return any(
            row.user.email == email and row.user.id != exclude_id
            for row in self.rows.values()
        )

    async def employee_link_taken(
        self, tenant_id: UUID, employee_id: UUID, exclude_id: UUID | None = None
    ) -> bool:
        return any(
            row.user.tenant_id == tenant_id
            and row.user.employee_id == employee_id
            and row.user.id != exclude_id
            for row in self.rows.values()
        )

    async def count_active_admins(self, tenant_id: UUID) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.user.tenant_id == tenant_id
            and row.user.active
            and row.user.role in ADMIN_ROLES
        )

    async def list(
        self, tenant_id: UUID, filters: UserFilters, page: PageParams
    ) -> tuple[Sequence[User], int]:
        rows = [
            row.user
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        ]
        if filters.q:
            needle = filters.q.lower()
            rows = [row for row in rows if needle in row.email.lower()]
        if filters.role is not None:
            rows = [row for row in rows if row.role == filters.role]
        if filters.active is not None:
            rows = [row for row in rows if row.active is filters.active]
        rows.sort(key=lambda row: row.email)  # pinned default order: email asc
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

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
        if await self.email_taken(email):  # DB unique-constraint backstop
            raise UserEmailTakenError()
        user = User(
            id=user_id,
            tenant_id=tenant_id,
            email=email,
            role=role,
            employee_id=employee_id,
            active=active,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        self.rows[(tenant_id, user_id)] = UserCredentials(
            user=user, password_hash=password_hash
        )
        return user

    async def update(
        self, tenant_id: UUID, user_id: UUID, **changes: Any
    ) -> User | None:
        key = (tenant_id, user_id)
        row = self.rows.get(key)
        if row is None:
            return None
        if "email" in changes and await self.email_taken(
            changes["email"], exclude_id=user_id
        ):
            raise UserEmailTakenError()
        user_changes = {
            field: value for field, value in changes.items() if field != "password_hash"
        }
        updated = row.user.model_copy(update={**user_changes, "updated_at": CREATED_AT})
        self.rows[key] = UserCredentials(
            user=updated,
            password_hash=changes.get("password_hash", row.password_hash),
        )
        return updated


class FakeSessionRepo:
    """Sessions keyed by their token hash (the real lookup key)."""

    def __init__(self) -> None:
        self.rows: dict[str, Session] = {}

    async def add(
        self,
        tenant_id: UUID,
        session_id: UUID,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> Session:
        row = Session(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=token_hash,
            created_at=CREATED_AT,
            expires_at=expires_at,
        )
        self.rows[token_hash] = row
        return row

    async def get_by_token_hash(self, token_hash: str, now: datetime) -> Session | None:
        row = self.rows.get(token_hash)
        if row is None or row.expires_at <= now:  # expired rows are rejected
            return None
        return row

    async def delete(self, token_hash: str) -> None:
        self.rows.pop(token_hash, None)

    async def delete_for_user(self, tenant_id: UUID, user_id: UUID) -> int:
        keys = [
            key
            for key, row in self.rows.items()
            if row.tenant_id == tenant_id and row.user_id == user_id
        ]
        for key in keys:
            del self.rows[key]
        return len(keys)


class FakeEmployeeLinkPort:
    """Settable existence-only port over the employees domain (ADR 0007)."""

    def __init__(self) -> None:
        self.employee_ids: set[tuple[UUID, UUID]] = set()

    def seed(self, tenant_id: UUID, employee_id: UUID) -> None:
        self.employee_ids.add((tenant_id, employee_id))

    async def exists(self, tenant_id: UUID, employee_id: UUID) -> bool:
        return (tenant_id, employee_id) in self.employee_ids
