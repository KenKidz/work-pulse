"""Readiness probe logic (deployment T1) — no database required."""

import asyncio
import time

import pytest

import app.main as main_module
from app.shared.errors import DependencyUnavailableError


class _OkConnection:
    async def execute(self, statement: object) -> None:
        assert "SELECT 1" in str(statement)


class _HungConnection:
    async def execute(self, statement: object) -> None:
        await asyncio.sleep(30)


class _ConnectContext:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def __aenter__(self) -> object:
        return self._connection

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _Engine:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    def connect(self) -> _ConnectContext:
        return _ConnectContext(self._connection)


async def test_probe_succeeds_when_postgres_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "get_engine", lambda: _Engine(_OkConnection()))

    await main_module.check_database_ready(timeout_seconds=1.0)


async def test_probe_maps_connection_failure_to_house_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable_engine() -> None:
        raise RuntimeError("postgresql://user:secret@db-internal:5432/workpulse")

    monkeypatch.setattr(main_module, "get_engine", unreachable_engine)

    with pytest.raises(DependencyUnavailableError) as excinfo:
        await main_module.check_database_ready(timeout_seconds=1.0)

    assert excinfo.value.code == "dependency_unavailable"
    # Driver/DSN text is never echoed to the caller.
    assert "secret" not in str(excinfo.value)
    assert "db-internal" not in str(excinfo.value)


async def test_probe_is_bounded_by_the_readiness_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "get_engine", lambda: _Engine(_HungConnection()))

    started = time.monotonic()
    with pytest.raises(DependencyUnavailableError):
        await main_module.check_database_ready(timeout_seconds=0.05)

    assert time.monotonic() - started < 1.0


def test_default_readiness_timeout_is_two_seconds() -> None:
    assert main_module.settings.readyz_timeout_seconds == 2.0
