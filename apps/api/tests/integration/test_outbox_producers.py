"""T5 integration — the employee producers write through the outbox.

Against real PostgreSQL (`postgres-test`):

- **AC2 (sweep)**: each of the 7 catalogued transitions driven through the
  HTTP API leaves exactly one `event_outbox` row per emitted envelope (two for
  a late check-in: `checked_in` then `late`), stamped with the acting
  identity's `tenant_id` and carrying that identity as `actor_id`.
- **AC5**: rows written under two tenants never diverge from their envelopes'
  `tenant_id` or the writing identity's.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    USER,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)

CLOCK = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)  # the late case's edge clock
SHIFT_PAYLOAD = {
    "name": "Always past",
    "start_time": "00:00",
    "end_time": "23:59",
    "grace_minutes": 0,
}
TRANSFERRED_ON = "2026-06-01"  # <= today, >= hired_at (established API-test date)
TERMINATED_ON = "2026-09-01"

Refs = tuple[str, str, str]
Driver = Callable[
    [AsyncClient, AsyncSession, Refs, Refs, pytest.MonkeyPatch], Awaitable[None]
]


async def _seed_actor(session: AsyncSession, refs: Refs) -> None:
    """Give the default actor an employee row in tenant A (self-service)."""
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        ACTOR,
        employee_code="EMP-00009",
        first_name="Actor",
        last_name="Self",
        email="self@example.test",
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


async def _create(client: AsyncClient, refs: Refs) -> dict[str, object]:
    response = await client.post("/api/v1/employees", json=employee_payload(refs))
    assert response.status_code == 201
    return response.json()


# --- drivers: one HTTP-API interaction per catalogued transition --------------


async def _drive_employee_created(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create(client, refs_a)


async def _drive_employee_updated(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create(client, refs_a)
    response = await client.patch(
        f"/api/v1/employees/{created['id']}", json={"first_name": "Augusta"}
    )
    assert response.status_code == 200


async def _drive_employee_transferred(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create(client, refs_a)
    response = await client.post(
        f"/api/v1/employees/{created['id']}/transfers",
        json={
            "department_id": refs_b[0],
            "effective_from": TRANSFERRED_ON,
            "reason": "reorg",
        },
    )
    assert response.status_code == 200


async def _drive_employee_terminated(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create(client, refs_a)
    response = await client.post(
        f"/api/v1/employees/{created['id']}/termination",
        json={"effective_date": TERMINATED_ON, "reason": "left"},
    )
    assert response.status_code == 200


async def _drive_attendance_checked_in(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_actor(session, refs_a)
    response = await client.post(
        "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
    )
    assert response.status_code == 201


async def _drive_attendance_checked_out(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_actor(session, refs_a)
    opened = (
        await client.post("/api/v1/attendance/check-ins", json={"work_mode": "onsite"})
    ).json()
    response = await client.post(
        f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
    )
    assert response.status_code == 200


async def _drive_attendance_late(
    client: AsyncClient,
    session: AsyncSession,
    refs_a: Refs,
    refs_b: Refs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_actor(session, refs_a)
    # Pin the edge clock past a 00:00 + 0-grace shift so lateness is a fact of
    # the test, not of the wall clock.
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    shift = await client.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
    assert shift.status_code == 201
    response = await client.post(
        "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
    )
    assert response.status_code == 201
    assert response.json()["late"] is True


SWEEP_CASES: list[tuple[str, list[str], Driver]] = [
    # expected_rows is the full committed sequence: setup mutations (e.g. the
    # employee a PATCH needs) emit their own envelopes, and every emitted
    # envelope must have exactly one row — nothing more, nothing less.
    ("employee.created", ["employee.created"], _drive_employee_created),
    (
        "employee.updated",
        ["employee.created", "employee.updated"],
        _drive_employee_updated,
    ),
    (
        "employee.transferred",
        ["employee.created", "employee.transferred"],
        _drive_employee_transferred,
    ),
    (
        "employee.terminated",
        ["employee.created", "employee.terminated"],
        _drive_employee_terminated,
    ),
    ("attendance.checked_in", ["attendance.checked_in"], _drive_attendance_checked_in),
    (
        "attendance.checked_out",
        ["attendance.checked_in", "attendance.checked_out"],
        _drive_attendance_checked_out,
    ),
    # A late check-in emits two envelopes — one row each, builder order.
    (
        "attendance.late",
        ["attendance.checked_in", "attendance.late"],
        _drive_attendance_late,
    ),
]


@pytest.mark.parametrize(
    ("expected_rows", "drive"),
    [(expected, drive) for _, expected, drive in SWEEP_CASES],
    ids=[name for name, _, _ in SWEEP_CASES],
)
async def test_each_transition_writes_exactly_its_envelopes(
    expected_rows: list[str],
    drive: Driver,
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 — all 7 transitions, one row per emitted envelope, tenant-stamped."""
    async with client_factory() as admin:
        refs_a = await create_org_refs(admin)
        refs_b = await create_org_refs(admin, suffix="-B")

    async with client_factory() as client:
        await drive(client, session, refs_a, refs_b, monkeypatch)

    rows = await _outbox_rows(session)
    assert [row.event_type for row in rows] == expected_rows
    for row in rows:
        assert row.tenant_id == TENANT_A  # the acting identity's tenant
        assert UUID(row.payload["tenant_id"]) == row.tenant_id
        assert row.payload["event_type"] == row.event_type
        assert row.payload["actor_id"] == str(USER)
        assert row.payload["event_id"] == row.event_id
        assert row.published_at is None
        assert row.attempts == 0
        assert row.last_error is None


async def test_two_tenants_stamp_their_own_rows(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC5 — a row's tenant never diverges from its envelope's or the writer's."""
    async with client_factory() as tenant_a_admin:
        refs_a = await create_org_refs(tenant_a_admin)
        created_a = (
            await tenant_a_admin.post(
                "/api/v1/employees", json=employee_payload(refs_a)
            )
        ).json()

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin)
        created_b = (
            await tenant_b_admin.post(
                "/api/v1/employees", json=employee_payload(refs_b)
            )
        ).json()

    rows = await _outbox_rows(session)
    assert [row.event_type for row in rows] == ["employee.created", "employee.created"]

    by_employee = {row.payload["data"]["employee_id"]: row for row in rows}
    assert set(by_employee) == {created_a["id"], created_b["id"]}
    for row in rows:
        # Row and envelope agree on the tenant — always.
        assert UUID(row.payload["tenant_id"]) == row.tenant_id
    assert by_employee[created_a["id"]].tenant_id == TENANT_A
    assert by_employee[created_b["id"]].tenant_id == TENANT_B
