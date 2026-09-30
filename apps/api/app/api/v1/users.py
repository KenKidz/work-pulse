"""Users routes: the 7 user-management endpoints pinned in the feature spec
(`.ai/features/identity-access.md`).

Thin by rule: identity → policy → service; `now` is taken at this edge only.
Every route requires the "Manage users, roles, permissions" capability
(admin/super_admin — `ensure_can_manage_users`); other roles fail closed with
`403 forbidden`. operationIds are pinned and drive the orval-generated web
hooks. There is no `DELETE`: deactivation is the archive action.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, UserServiceDep
from app.domains.identity.policies import ensure_can_manage_users
from app.domains.identity.repository import UserFilters
from app.domains.identity.schemas import (
    CreateUser,
    ResetPasswordRequest,
    UpdateUser,
    User,
    UserListPage,
)
from app.shared.identity import Role
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.get("/users", response_model=UserListPage, operation_id="listUsers")
async def list_users(
    identity: IdentityDep,
    service: UserServiceDep,
    page: PageParamsDep,
    q: str | None = Query(default=None, max_length=200),
    role: Role | None = Query(default=None),
    active: bool | None = Query(default=None),
) -> UserListPage:
    """Tenant-scoped users; default order `email` ascending (pinned)."""
    ensure_can_manage_users(identity)
    filters = UserFilters(q=q, role=role, active=active)
    return await service.list(identity, filters, page)


@router.post("/users", response_model=User, operation_id="createUser", status_code=201)
async def create_user(
    payload: CreateUser,
    identity: IdentityDep,
    service: UserServiceDep,
) -> User:
    """Admin-provisioned account; the optional employee link must be unlinked."""
    ensure_can_manage_users(identity)
    return await service.create(identity, payload, _now())


@router.get("/users/{id}", response_model=User, operation_id="getUser")
async def get_user(
    id: UUID,
    identity: IdentityDep,
    service: UserServiceDep,
) -> User:
    """Cross-tenant ids answer exactly like missing ones (`404 user_not_found`)."""
    ensure_can_manage_users(identity)
    return await service.get(identity, id)


@router.patch("/users/{id}", response_model=User, operation_id="updateUser")
async def update_user(
    id: UUID,
    payload: UpdateUser,
    identity: IdentityDep,
    service: UserServiceDep,
) -> User:
    """PATCH semantics (pinned): absent = unchanged, `employee_id: null` unlinks.

    `active` is never in PATCH — use the explicit activate/deactivate routes.
    """
    ensure_can_manage_users(identity)
    return await service.update(identity, id, payload, _now())


@router.post("/users/{id}/activate", response_model=User, operation_id="activateUser")
async def activate_user(
    id: UUID,
    identity: IdentityDep,
    service: UserServiceDep,
) -> User:
    """Restore access (idempotent `200` no-op when already active)."""
    ensure_can_manage_users(identity)
    return await service.activate(identity, id, _now())


@router.post(
    "/users/{id}/deactivate", response_model=User, operation_id="deactivateUser"
)
async def deactivate_user(
    id: UUID,
    identity: IdentityDep,
    service: UserServiceDep,
) -> User:
    """Revoke access (`409 last_admin` when it would strand the tenant)."""
    ensure_can_manage_users(identity)
    return await service.deactivate(identity, id, _now())


@router.post("/users/{id}/password", status_code=204, operation_id="resetUserPassword")
async def reset_user_password(
    id: UUID,
    payload: ResetPasswordRequest,
    identity: IdentityDep,
    service: UserServiceDep,
) -> None:
    """Admin reset (no current password) — revokes every session of the user."""
    ensure_can_manage_users(identity)
    await service.reset_password(identity, id, payload, _now())
