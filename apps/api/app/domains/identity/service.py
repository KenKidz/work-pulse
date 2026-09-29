"""Use cases for the Identity domain — the only place its business logic lives.

Rules implemented here (pinned in `.ai/features/identity-access.md`):

- Login is email + password with **one** failure path: unknown email, wrong
  password and a deactivated account all raise `invalid_credentials` with an
  identical static body — no enumeration signal. The account resolves the
  tenant (emails are unique globally, decision 7).
- Sessions are opaque 256-bit tokens stored hashed (SHA-256 lookup key only)
  with an absolute TTL; logout deletes the row and is idempotent. Every
  request re-resolves user + role from the session, so role changes and
  deactivation take effect immediately.
- Password changes require the current password (`invalid_password` 400); both
  change and admin reset **revoke every session** of the user. Password
  material never reaches responses, errors, logs or audit entries.
- User management: global email uniqueness (`user_email_taken`), employee
  links must exist in the tenant (`employee_not_found`) and be unlinked
  (`employee_link_taken`), deactivating the last active admin/super_admin is
  rejected (`last_admin`). PATCH semantics: absent = unchanged, explicit
  `"employee_id": null` unlinks.
- `user.*` audit actions record field *names* only for PII and never carry
  password material; login/logout are not audited.
- Time is a parameter (`now`) — this module never reads the wall clock.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.identity.credentials import (
    hash_password,
    new_session_token,
    session_token_hash,
    verify_password,
)
from app.domains.identity.exceptions import (
    EmployeeLinkTakenError,
    InvalidCredentialsError,
    InvalidPasswordError,
    LastAdminError,
    UnauthenticatedError,
    UserEmailTakenError,
    UserNotFoundError,
)
from app.domains.identity.policies import ADMIN_ROLES
from app.domains.identity.repository import (
    EmployeeLinkPort,
    SessionRepository,
    UserFilters,
    UserRepository,
)
from app.domains.identity.schemas import (
    ChangePasswordRequest,
    CreateUser,
    LoginRequest,
    ResetPasswordRequest,
    UpdateUser,
    User,
    UserCredentials,
    UserListPage,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import DevIdentity
from app.shared.pagination import PageParams


def _ref(value: UUID | None) -> str | None:
    """Audit-friendly id value (never PII)."""
    return str(value) if value is not None else None


class AuthService:
    """Authentication use cases: login, logout, session resolution, passwords."""

    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        audit: AuditWriter,
        session_ttl: timedelta,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._audit = audit
        self._session_ttl = session_ttl

    async def login(self, payload: LoginRequest, now: datetime) -> tuple[User, str]:
        """Email + password → (user, raw session token).

        Unknown email, wrong password and a deactivated account are one failure
        path (`invalid_credentials`, byte-identical bodies); the account
        resolves the tenant. The raw token goes into the cookie — only its
        SHA-256 hash is persisted.
        """
        credentials = await self._users.get_by_email(payload.email)
        if credentials is None or not credentials.user.active:
            raise InvalidCredentialsError()
        if not await verify_password(credentials.password_hash, payload.password):
            raise InvalidCredentialsError()

        token = new_session_token()
        await self._sessions.add(
            credentials.user.tenant_id,
            uuid7(),
            user_id=credentials.user.id,
            token_hash=session_token_hash(token),
            expires_at=now + self._session_ttl,
        )
        return credentials.user, token

    async def logout(self, token: str | None) -> None:
        """Delete the session row; without a token this is an idempotent no-op."""
        if token is None:
            return
        await self._sessions.delete(session_token_hash(token))

    async def me(self, token: str | None, now: datetime) -> User:
        """Resolve the session cookie to its user; 401 on anything unusable."""
        credentials = await self._resolve_session(token, now)
        return credentials.user

    async def change_password(
        self,
        token: str | None,
        payload: ChangePasswordRequest,
        now: datetime,
    ) -> None:
        """Verify the current password, rehash, then revoke every session."""
        credentials = await self._resolve_session(token, now)
        if not await verify_password(
            credentials.password_hash, payload.current_password
        ):
            raise InvalidPasswordError()

        user = credentials.user
        password_hash = await hash_password(payload.new_password)
        updated = await self._users.update(
            user.tenant_id, user.id, password_hash=password_hash
        )
        if updated is None:  # pragma: no cover — lost race between get and update
            raise UnauthenticatedError()
        await self._sessions.delete_for_user(user.tenant_id, user.id)
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=user.tenant_id,
                actor_id=user.id,
                action="user.password_changed",
                entity_type="user",
                entity_id=user.id,
                occurred_at=now,
                # PII rule: no password material, not even the hash.
                changes={"password": "changed"},
            )
        )

    async def _resolve_session(
        self, token: str | None, now: datetime
    ) -> UserCredentials:
        """Cookie token → `UserCredentials`; fail closed (401 unauthenticated).

        Missing, unknown, expired or revoked session, or a deactivated user —
        all collapse into the same failure.
        """
        if token is None:
            raise UnauthenticatedError()
        session = await self._sessions.get_by_token_hash(session_token_hash(token), now)
        if session is None:
            raise UnauthenticatedError()
        credentials = await self._users.get_credentials(
            session.tenant_id, session.user_id
        )
        if credentials is None or not credentials.user.active:
            raise UnauthenticatedError()
        return credentials


class UserService:
    """Admin-provisioned user accounts (create/update/activate/password reset)."""

    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        links: EmployeeLinkPort,
        audit: AuditWriter,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._links = links
        self._audit = audit

    # --- reads ---------------------------------------------------------------

    async def get(self, identity: DevIdentity, user_id: UUID) -> User:
        """Tenant-scoped fetch — cross-tenant ids answer like missing ones."""
        user = await self._users.get(identity.tenant_id, user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    async def list(
        self,
        identity: DevIdentity,
        filters: UserFilters,
        page: PageParams,
    ) -> UserListPage:
        items, total = await self._users.list(identity.tenant_id, filters, page)
        return UserListPage(items=list(items), page=page.make_page(total))

    # --- writes --------------------------------------------------------------

    async def create(
        self, identity: DevIdentity, payload: CreateUser, now: datetime
    ) -> User:
        tenant_id = identity.tenant_id
        # Global uniqueness (decision 7) — an email held by another tenant is a
        # conflict here too; the DB index is the race backstop.
        if await self._users.email_taken(payload.email):
            raise UserEmailTakenError()
        if payload.employee_id is not None:
            await self._ensure_link_free(tenant_id, payload.employee_id, None)

        created = await self._users.add(
            tenant_id,
            uuid7(),
            email=payload.email,
            role=payload.role,
            employee_id=payload.employee_id,
            password_hash=await hash_password(payload.password),
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="user.created",
                entity_type="user",
                entity_id=created.id,
                occurred_at=now,
                changes={
                    "email": "changed",  # PII: field name only, never the address
                    "role": {"old": None, "new": created.role.value},
                    "employee_id": {
                        "old": None,
                        "new": _ref(created.employee_id),
                    },
                },
            )
        )
        return created

    async def update(
        self,
        identity: DevIdentity,
        user_id: UUID,
        payload: UpdateUser,
        now: datetime,
    ) -> User:
        tenant_id = identity.tenant_id
        existing = await self.get(identity, user_id)
        # PATCH semantics: absent fields are unchanged; an explicitly provided
        # `"employee_id": null` unlinks (the schema pins the rest).
        changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        if "email" in changes and changes["email"] != existing.email:
            if await self._users.email_taken(changes["email"], exclude_id=user_id):
                raise UserEmailTakenError()
        if "employee_id" in changes and changes["employee_id"] is not None:
            await self._ensure_link_free(
                tenant_id, changes["employee_id"], exclude_id=user_id
            )

        updated = await self._users.update(tenant_id, user_id, **changes)
        if updated is None:  # pragma: no cover — lost race between get and update
            raise UserNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="user.updated",
                entity_type="user",
                entity_id=user_id,
                occurred_at=now,
                # PII rule: names only — a role change is recorded here too.
                changes={field: "changed" for field in changes},
            )
        )
        return updated

    async def activate(
        self, identity: DevIdentity, user_id: UUID, now: datetime
    ) -> User:
        existing = await self.get(identity, user_id)
        if existing.active:
            return existing  # 200 no-op, no audit entry (archive/restore idiom)

        updated = await self._users.update(identity.tenant_id, user_id, active=True)
        if updated is None:  # pragma: no cover — lost race between get and update
            raise UserNotFoundError()
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="user.activated",
                entity_type="user",
                entity_id=user_id,
                occurred_at=now,
                changes={"active": {"old": False, "new": True}},
            )
        )
        return updated

    async def deactivate(
        self, identity: DevIdentity, user_id: UUID, now: datetime
    ) -> User:
        existing = await self.get(identity, user_id)
        if not existing.active:
            return existing  # 200 no-op, no audit entry

        if (
            existing.role in ADMIN_ROLES
            and await self._users.count_active_admins(identity.tenant_id) <= 1
        ):
            # A tenant must retain at least one active admin/super_admin.
            raise LastAdminError()

        updated = await self._users.update(identity.tenant_id, user_id, active=False)
        if updated is None:  # pragma: no cover — lost race between get and update
            raise UserNotFoundError()
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="user.deactivated",
                entity_type="user",
                entity_id=user_id,
                occurred_at=now,
                changes={"active": {"old": True, "new": False}},
            )
        )
        return updated

    async def reset_password(
        self,
        identity: DevIdentity,
        user_id: UUID,
        payload: ResetPasswordRequest,
        now: datetime,
    ) -> None:
        """Admin reset: rehash and revoke every session (no current password)."""
        tenant_id = identity.tenant_id
        await self.get(identity, user_id)  # tenant-scoped 404

        updated = await self._users.update(
            tenant_id, user_id, password_hash=await hash_password(payload.new_password)
        )
        if updated is None:  # pragma: no cover — lost race between get and update
            raise UserNotFoundError()
        await self._sessions.delete_for_user(tenant_id, user_id)
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=tenant_id,
                actor_id=identity.actor_id,
                action="user.password_reset",
                entity_type="user",
                entity_id=user_id,
                occurred_at=now,
                changes={"password": "changed"},
            )
        )

    # --- shared checks ---------------------------------------------------------

    async def _ensure_link_free(
        self, tenant_id: UUID, employee_id: UUID, exclude_id: UUID | None
    ) -> None:
        """Pinned employee-link rules: exists in the tenant, not already linked.

        Employment lifecycle (e.g. a terminated employee) is deliberately not
        judged here — accounts and employment are separate lifecycles.
        """
        if not await self._links.exists(tenant_id, employee_id):
            raise EmployeeNotFoundError()
        if await self._users.employee_link_taken(
            tenant_id, employee_id, exclude_id=exclude_id
        ):
            raise EmployeeLinkTakenError()
