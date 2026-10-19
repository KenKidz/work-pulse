"""Attendance × Leave integration (Leave spec T5, ADR 0014).

Approved leave is seeded through the leave repository (the leave routers land
in T6); the attendance surfaces must then refuse check-in on covered days and
project covered employees as `on_leave`, overriding same-day records and
excluding `absent`. All clock reads are injected (`attendance_api._now`).
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.api.v1 import attendance as attendance_api
from app.domains.attendance.service import AttendanceService
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.leave.schemas import LeaveRequestStatus, LeaveTypeCode
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.repositories.attendance import (
    PostgresAttendanceRepository,
    PostgresShiftRepository,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.leave import PostgresLeaveRepository
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
from tests.unit.outbox.fakes import RecordingEventOutbox

BOARD_DATE = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


async def _seed_actor(session: AsyncSession, refs: tuple[str, str, str]) -> None:
    """Give the default actor an employee row in tenant A (self check-in)."""
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


async def _add_leave_request(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    employee_id: UUID,
    start_date: date,
    end_date: date,
) -> UUID:
    """Seed a `PENDING` request directly through the leave repository.

    `unpaid` is used so no balance row is involved — the attendance surfaces
    only read the status and the date range.
    """
    repo = PostgresLeaveRepository(session)
    types = await repo.ensure_leave_types(tenant_id)
    unpaid = next(row for row in types if row.code == LeaveTypeCode.UNPAID)
    request = await repo.add_request(
        tenant_id,
        uuid7(),
        employee_id=employee_id,
        leave_type_id=unpaid.id,
        start_date=start_date,
        end_date=end_date,
        days=(end_date - start_date).days + 1,
        reason=None,
    )
    return request.id


async def _approve_leave(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    employee_id: UUID,
    start_date: date,
    end_date: date,
) -> None:
    """Seed an APPROVED request (the only status that reaches the board)."""
    request_id = await _add_leave_request(
        session,
        tenant_id=tenant_id,
        employee_id=employee_id,
        start_date=start_date,
        end_date=end_date,
    )
    approved = await PostgresLeaveRepository(session).transition_request(
        tenant_id,
        request_id,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.APPROVED,
        now=NOW,
        decided_by=USER,
        decided_at=NOW,
    )
    assert approved is not None
    assert approved.status == LeaveRequestStatus.APPROVED


async def test_check_in_on_an_approved_leave_day_is_conflict(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)
    monkeypatch.setattr(attendance_api, "_now", lambda: NOW)
    await _approve_leave(
        session,
        tenant_id=TENANT_A,
        employee_id=ACTOR,
        start_date=BOARD_DATE,
        end_date=BOARD_DATE,
    )

    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )

    # Leave spec decision 9: `ON_LEAVE` and `PRESENT` are contradictory.
    _assert_error(response, 409, "employee_on_leave")


async def test_pending_leave_does_not_block_check_in(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only `APPROVED` coverage blocks — `PENDING` never reaches the wall."""
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)
    monkeypatch.setattr(attendance_api, "_now", lambda: NOW)
    await _add_leave_request(
        session,
        tenant_id=TENANT_A,
        employee_id=ACTOR,
        start_date=BOARD_DATE,
        end_date=BOARD_DATE,
    )

    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )

    assert response.status_code == 201


async def test_check_in_allowed_when_the_leave_range_does_not_cover_today(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Range boundaries: ending yesterday / starting tomorrow never count."""
    async with client_factory() as client:
        refs = await create_org_refs(client)
        grace = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Grace",
                    last_name="Hopper",
                    email="grace@example.test",
                ),
            )
        ).json()
    await _seed_actor(session, refs)
    monkeypatch.setattr(attendance_api, "_now", lambda: NOW)
    # ACTOR's leave ended yesterday; Grace's starts tomorrow.
    await _approve_leave(
        session,
        tenant_id=TENANT_A,
        employee_id=ACTOR,
        start_date=date(2026, 9, 22),
        end_date=date(2026, 9, 23),
    )
    await _approve_leave(
        session,
        tenant_id=TENANT_A,
        employee_id=UUID(grace["id"]),
        start_date=date(2026, 9, 25),
        end_date=date(2026, 9, 26),
    )

    async with client_factory() as client:
        actor_check_in = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
    async with client_factory(identity(employee_id=UUID(grace["id"]))) as grace_client:
        grace_check_in = await grace_client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )

    assert actor_check_in.status_code == 201
    assert actor_check_in.json()["workday"] == BOARD_DATE.isoformat()
    assert grace_check_in.status_code == 201


async def test_board_on_leave_overrides_records_and_excludes_absent(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coexistence rule (ADR 0014) end to end on the board surfaces."""
    async with client_factory() as client:
        refs = await create_org_refs(client)
        grace = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Grace",
                    last_name="Hopper",
                    email="grace@example.test",
                ),
            )
        ).json()
        # An expected shift would make a recordless employee absent at 11:00.
        shift = await client.post(
            "/api/v1/shifts",
            json={
                "name": "Always past",
                "start_time": "00:00",
                "end_time": "23:59",
                "grace_minutes": 0,
            },
        )
        assert shift.status_code == 201
    await _seed_actor(session, refs)
    monkeypatch.setattr(attendance_api, "_now", lambda: NOW)

    # Grace checks in *before* the approval — a record can pre-date leave.
    async with client_factory(identity(employee_id=UUID(grace["id"]))) as grace_client:
        grace_record = (
            await grace_client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    assert grace_record["late"] is True  # 11:00 past a 00:00 + 0-grace shift
    await _approve_leave(
        session,
        tenant_id=TENANT_A,
        employee_id=ACTOR,  # recordless — must not be absent
        start_date=BOARD_DATE,
        end_date=BOARD_DATE,
    )
    await _approve_leave(
        session,
        tenant_id=TENANT_A,
        employee_id=UUID(grace["id"]),  # same-day record — on_leave wins
        start_date=BOARD_DATE,
        end_date=BOARD_DATE,
    )

    async with client_factory() as admin:
        wire = (
            await admin.get(
                "/api/v1/attendance/board", params={"date": BOARD_DATE.isoformat()}
            )
        ).json()
        history = (
            await admin.get(
                "/api/v1/attendance/records",
                params={"employee_id": grace["id"]},
            )
        ).json()

    assert wire["counts"] == {
        "present": 0,  # Grace's record contributes no record-based count
        "remote": 0,
        "late": 0,  # her late flag rides the overridden record, not the board
        "on_leave": 2,
        "absent": 0,  # ACTOR is covered, never absent
        "checked_out": 0,
    }

    service = AttendanceService(
        repo=PostgresAttendanceRepository(session),
        shifts=PostgresShiftRepository(session),
        audit=PostgresAuditWriter(session),
        outbox=RecordingEventOutbox(),
    )
    detail = await service.board_detail(identity(), BOARD_DATE, NOW)
    rows = {row.employee_id: row for row in detail.rows}
    assert set(rows) == {ACTOR, UUID(grace["id"])}
    for row in rows.values():
        assert row.status == "on_leave"
        assert row.late is False
        assert row.minutes_late is None
        assert row.since is None
        assert row.attendance_id is None
    # `counts == recount(rows)` holds with coverage in play.
    assert detail.counts.model_dump() == {
        "present": sum(row.status == "present" for row in detail.rows),
        "remote": sum(row.status == "remote" for row in detail.rows),
        "late": sum(row.late for row in detail.rows),
        "on_leave": sum(row.status == "on_leave" for row in detail.rows),
        "absent": sum(row.status == "absent" for row in detail.rows),
        "checked_out": sum(row.status == "checked_out" for row in detail.rows),
    }

    # The overridden record stays in the history and closing it stays allowed.
    assert history["page"]["total_items"] == 1
    assert history["items"][0]["id"] == grace_record["id"]
    async with client_factory(identity(employee_id=UUID(grace["id"]))) as grace_client:
        check_out = await grace_client.post(
            f"/api/v1/attendance/records/{grace_record['id']}/check-out", json={}
        )
    assert check_out.status_code == 200


async def test_leave_coverage_is_tenant_scoped(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        refs_b = await create_org_refs(tenant_b)
        b_employee = (
            await tenant_b.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs_b,
                    first_name="Bea",
                    last_name="Bee",
                    email="bea@example.test",
                ),
            )
        ).json()
    async with client_factory() as client:
        refs_a = await create_org_refs(client)
    await _seed_actor(session, refs_a)
    monkeypatch.setattr(attendance_api, "_now", lambda: NOW)
    await _approve_leave(
        session,
        tenant_id=TENANT_B,
        employee_id=UUID(b_employee["id"]),
        start_date=BOARD_DATE,
        end_date=BOARD_DATE,
    )

    # B's own coverage blocks B's employee…
    async with client_factory(
        identity(tenant=TENANT_B, employee_id=UUID(b_employee["id"]))
    ) as b_client:
        b_check_in = await b_client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
    # …while tenant A neither sees it in coverage nor on its board.
    async with client_factory() as a_client:
        a_check_in = await a_client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        a_board = (
            await a_client.get(
                "/api/v1/attendance/board", params={"date": BOARD_DATE.isoformat()}
            )
        ).json()

    _assert_error(b_check_in, 409, "employee_on_leave")
    assert a_check_in.status_code == 201
    assert a_board["counts"]["on_leave"] == 0
    assert a_board["counts"]["present"] == 1
