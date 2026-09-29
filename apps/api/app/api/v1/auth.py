"""Auth routes: the 4 endpoints pinned in `.ai/features/identity-access.md`.

Thin by rule: the identity comes from the `wp_session` cookie (resolved by the
session resolver in `app/dependencies.py`), `now` is taken at this edge only,
and all logic lives in `AuthService`. Login/logout are not audited (not
workforce mutations; failed logins are never stored). operationIds are pinned
and drive the orval-generated web hooks.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response

from app.config import Settings
from app.dependencies import (
    AuthServiceDep,
    CurrentUserDep,
    SessionTokenDep,
    SettingsDep,
)
from app.domains.identity.credentials import SESSION_COOKIE_NAME
from app.domains.identity.schemas import ChangePasswordRequest, LoginRequest, User

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


def _session_cookie_kwargs(settings: Settings) -> dict[str, Any]:
    """The pinned cookie attributes, shared by set and delete (one source)."""
    return {
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.session_cookie_secure,
    }


@router.post("/auth/login", response_model=User, operation_id="login")
async def login(
    payload: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
) -> User:
    """Email + password → `200 User` + the `wp_session` cookie.

    Unknown email, wrong password and a deactivated account are one failure
    path (`401 invalid_credentials`, byte-identical bodies — no enumeration).
    """
    user, token = await service.login(payload, _now())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        **_session_cookie_kwargs(settings),
    )
    return user


@router.post("/auth/logout", status_code=204, operation_id="logout")
async def logout(
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    token: SessionTokenDep = None,
) -> None:
    """Delete the session row and clear the cookie; idempotent (204) alone."""
    await service.logout(token)
    response.delete_cookie(key=SESSION_COOKIE_NAME, **_session_cookie_kwargs(settings))


@router.get("/auth/me", response_model=User, operation_id="getCurrentUser")
async def get_me(user: CurrentUserDep) -> User:
    """The authenticated user — the session resolves exactly as elsewhere."""
    return user


@router.put("/auth/password", status_code=204, operation_id="changePassword")
async def change_password(
    payload: ChangePasswordRequest,
    service: AuthServiceDep,
    token: SessionTokenDep = None,
) -> None:
    """Change own password (current password required) — revokes all sessions."""
    await service.change_password(token, payload, _now())
