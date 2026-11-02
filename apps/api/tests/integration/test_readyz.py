"""`GET /readyz` over HTTP against the real throwaway PostgreSQL (deployment T1).

Success runs `SELECT 1` on the real `postgres-test` engine; the failure cases
swap `app.main.get_engine` for an unreachable engine and for a hung one, proving
the house 503 and the `READYZ_TIMEOUT_SECONDS` bound without touching the shared
test engine/session.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

import app.main as main_module
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client


async def test_readyz_answers_ready_without_auth(client: AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_readyz_returns_house_503_when_postgres_is_unreachable(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreachable = create_async_engine(
        "postgresql+asyncpg://workpulse:workpulse@127.0.0.1:1/workpulse"
    )
    monkeypatch.setattr(main_module, "get_engine", lambda: unreachable)
    try:
        response = await client.get("/readyz")
    finally:
        await unreachable.dispose()

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    # The response never names the dependency, the host or driver text.
    rendered = json.dumps(body)
    assert "127.0.0.1" not in rendered
    assert "asyncpg" not in rendered
    # Liveness stays dependency-free.
    assert (await client.get("/healthz")).status_code == 200


class _HungConnect:
    async def __aenter__(self) -> None:
        await asyncio.sleep(30)

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _HungEngine:
    def connect(self) -> _HungConnect:
        return _HungConnect()


async def test_readyz_is_bounded_when_postgres_hangs(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "get_engine", lambda: _HungEngine())
    monkeypatch.setattr(main_module.settings, "readyz_timeout_seconds", 0.1)

    started = time.monotonic()
    response = await client.get("/readyz")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert elapsed < 1.0
    assert (await client.get("/healthz")).status_code == 200
