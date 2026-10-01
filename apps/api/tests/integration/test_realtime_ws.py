"""WS handshake denial tests — a denied upgrade delivers zero application frames.

`starlette.testclient.TestClient` drives the ASGI app (upgrades included) in
its own portal loop. Both denial paths are deliberately pre-DB: a missing
cookie short-circuits inside `get_socket_identity` before any query, and the
role denial is injected through a dependency override — so the shared
integration session's event loop is never entered (the fixture note in the
T4 plan). A full in-process happy path would need a per-loop session override
and is covered end-to-end by the e2e suite instead.
"""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.dependencies import get_socket_identity
from app.main import app
from app.shared.identity import Role
from tests.integration.conftest import identity


def _expect_denied(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/realtime/board"):
            pytest.fail("a denied upgrade must not yield a session")


def test_upgrade_without_a_session_is_rejected_pre_accept() -> None:
    with TestClient(app) as client:
        _expect_denied(client)


def test_upgrade_with_an_insufficient_role_is_rejected_pre_accept() -> None:
    app.dependency_overrides[get_socket_identity] = lambda: identity(role=Role.EMPLOYEE)
    try:
        with TestClient(app) as client:
            _expect_denied(client)
    finally:
        app.dependency_overrides.pop(get_socket_identity, None)
