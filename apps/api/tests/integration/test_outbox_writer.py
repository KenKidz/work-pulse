"""T3 integration — the outbox writer against real PostgreSQL (`postgres-test`).

Covers the writer half of AC3: `append` flushes inside the request's
transaction and never commits on its own, so a mutation that fails after the
append (the `get_session` rollback-on-exception path) leaves no row behind —
the "Outbox, not dual writes" guarantee. Also proves stored payloads are the
builders' envelopes verbatim and `created_at` comes from the writer's injected
clock (time is a parameter; nothing is re-stamped).
"""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.attendance import events as attendance_events
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.session import get_session
from tests.integration.conftest import ACTOR, TENANT_A, USER

TENANT_ID = TENANT_A
ATTENDANCE_ID = UUID("00000000-0000-4000-8000-000000000004")
SHIFT_ID = UUID("00000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # the domain time in the envelope
WRITE_TIME = datetime(2026, 10, 16, 8, 30, tzinfo=UTC)  # the writer's clock
WORKDAY = date(2026, 9, 24)


def _clock() -> datetime:
    return WRITE_TIME


def _late_check_in_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    """The two envelopes a late check-in emits, in builder order."""
    checked_in = attendance_events.attendance_checked_in(
        tenant_id=TENANT_ID,
        actor_id=USER,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=ACTOR,
        workday=WORKDAY,
        work_mode="onsite",
        late=True,
        minutes_late=18,  # the same minutes the `late` envelope carries
    )
    late = attendance_events.attendance_late(
        tenant_id=TENANT_ID,
        actor_id=USER,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=ACTOR,
        workday=WORKDAY,
        shift_id=SHIFT_ID,
        minutes_late=18,
    )
    return checked_in, late


async def _count_rows(session: AsyncSession) -> int:
    return (
        await session.execute(select(func.count()).select_from(EventOutboxRow))
    ).scalar_one()


async def test_appended_rows_store_envelopes_verbatim(session: AsyncSession) -> None:
    outbox = PostgresEventOutbox(session, _clock)
    checked_in, late = _late_check_in_pair()

    await outbox.append(checked_in)
    await outbox.append(late)
    await session.commit()

    async with get_session_factory()() as fresh:
        rows = (
            (await fresh.execute(select(EventOutboxRow).order_by(EventOutboxRow.id)))
            .scalars()
            .all()
        )

    assert [row.event_type for row in rows] == [
        "attendance.checked_in",
        "attendance.late",
    ]
    assert rows[0].payload == checked_in
    assert rows[1].payload == late
    # Relay poll order (UUIDv7 `id`) preserves same-transaction builder order.
    assert rows[0].id < rows[1].id
    for row, envelope in ((rows[0], checked_in), (rows[1], late)):
        assert row.event_id == envelope["event_id"]
        assert row.tenant_id == TENANT_ID
        assert row.occurred_at == NOW  # never re-stamped
        assert row.created_at == WRITE_TIME  # injected clock, not server time
        assert row.published_at is None
        assert row.attempts == 0
        assert row.last_error is None


async def test_failed_transaction_leaves_nothing_behind() -> None:
    """AC3 (writer half) through the real request-session lifecycle.

    `athrow` is how FastAPI tears the `get_session` generator down when the
    request handler raises — exercising the production rollback path instead
    of emulating it.
    """
    # The runtime object is an async generator (FastAPI tears dependency
    # generators down with `athrow`); the port's annotation just narrows the
    # public type to `AsyncIterator`.
    lifecycle = cast(AsyncGenerator[AsyncSession, None], get_session())
    db_session = await anext(lifecycle)
    outbox = PostgresEventOutbox(db_session, _clock)
    await outbox.append(_late_check_in_pair()[0])

    # Flushed, never committed: invisible to any other transaction.
    async with get_session_factory()() as fresh:
        assert await _count_rows(fresh) == 0

    with pytest.raises(RuntimeError, match="mutation failed"):
        await lifecycle.athrow(RuntimeError("mutation failed"))

    async with get_session_factory()() as fresh:
        assert await _count_rows(fresh) == 0
