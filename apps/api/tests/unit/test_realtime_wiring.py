"""Unit tests for the WS wiring helpers — short-lived sessions, no held pool connection.

WebSocket handlers keep their dependencies until the socket closes, so the
production providers must never hand them a request-scoped session: a pinned
PostgreSQL connection per viewer would exhaust the pool (review finding 1).
These tests pin the open/close-per-read contract with a session-factory stub.
"""

from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from app import dependencies
from app.config import Settings
from app.domains.identity.schemas import User
from app.shared.identity import Identity, Role

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
USER_ID = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
TODAY = date(2026, 9, 24)


def _identity() -> Identity:
    return Identity(
        tenant_id=TENANT, actor_id=USER_ID, role=Role.ADMIN, employee_id=EMPLOYEE
    )


class _Session:
    """A stand-in AsyncSession: records enter/close, no I/O."""

    def __init__(self) -> None:
        self.entered = False
        self.closed = False

    async def __aenter__(self) -> "_Session":
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True


class _SessionFactory:
    """A stand-in async_sessionmaker: tracks every session it hands out."""

    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session()
        self.sessions.append(session)
        return session


class _StubAttendance:
    """The board port's shape, counting calls (never touches a session)."""

    def __init__(self) -> None:
        self.board_calls = 0
        self.scope_calls = 0

    async def board_detail(
        self, identity: Identity, board_date: date, now: datetime
    ) -> Any:
        self.board_calls += 1
        return ("detail", board_date)

    async def visible_employee_scope(
        self, identity: Identity
    ) -> frozenset[UUID] | None:
        self.scope_calls += 1
        return frozenset()


class _StubAuth:
    def __init__(self, user: User) -> None:
        self._user = user

    async def me(self, token: str | None, now: datetime) -> User:
        return self._user


def _user() -> User:
    return User(
        id=USER_ID,
        tenant_id=TENANT,
        email="admin@example.test",
        role=Role.ADMIN,
        employee_id=EMPLOYEE,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


async def test_board_reads_open_and_close_one_session_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAttendance()
    monkeypatch.setattr(dependencies, "get_attendance_service", lambda session: stub)
    factory = _SessionFactory()
    board = dependencies.ShortLivedAttendanceBoard(cast(Any, factory))

    detail = await board.board_detail(_identity(), TODAY, NOW)
    scope = await board.visible_employee_scope(_identity())

    assert detail == ("detail", TODAY)
    assert scope == frozenset()
    assert (stub.board_calls, stub.scope_calls) == (1, 1)
    assert len(factory.sessions) == 2  # one per read — never shared
    assert all(session.entered and session.closed for session in factory.sessions)


async def test_socket_identity_resolves_on_a_closed_short_lived_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dependencies, "get_auth_service", lambda session, settings: _StubAuth(_user())
    )
    factory = _SessionFactory()

    resolved = await dependencies.get_socket_identity(
        settings=Settings(), session_factory=cast(Any, factory), token="token"
    )

    assert resolved == _identity()
    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed  # never held past resolution


async def test_socket_identity_denies_without_opening_a_session() -> None:
    factory = _SessionFactory()

    resolved = await dependencies.get_socket_identity(
        settings=Settings(), session_factory=cast(Any, factory), token=None
    )

    assert resolved is None
    assert factory.sessions == []
