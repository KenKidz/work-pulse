"""HTTP integration tests for `GET /api/v1/realtime/board` — the REST snapshot.

The snapshot is the attendance board's one projection (`board_detail`, T3)
mapped to the realtime wire, so criterion 1 ("the two surfaces never
disagree") is asserted against `GET /attendance/board` and `board_detail` at
one injected instant. Scope, denials, tenant isolation and the Redis-free
snapshot path are pinned here; WS handshake denials live in
`test_realtime_ws.py`.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.api.v1 import realtime as realtime_api
from app.dependencies import get_realtime_service
from app.domains.attendance.service import AttendanceService
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.realtime.service import RealtimeService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.repositories.attendance import (
    PostgresAttendanceRepository,
    PostgresShiftRepository,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.presence import RedisPresencePublisher
from app.main import app
from app.shared.identity import Role
from app.shared.presence import PresenceFact
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)
from tests.unit.outbox.fakes import RecordingEventOutbox

B_ACTOR = UUID("00000000-0000-4000-8000-000000000012")

PINNED_SNAPSHOT_KEYS = {"date", "counts", "presence", "activity"}
PINNED_PRESENCE_ROW_KEYS = {
    "employee_id",
    "status",
    "late",
    "minutes_late",
    "since",
    "attendance_id",
}
PINNED_BOARD_COUNT_KEYS = {
    "present",
    "remote",
    "late",
    "on_leave",
    "absent",
    "checked_out",
}
ZERO_COUNTS = {
    "present": 0,
    "remote": 0,
    "late": 0,
    "on_leave": 0,
    "absent": 0,
    "checked_out": 0,
}

CLOCK = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)


class _ExplodingStream:
    """Fails loudly if the snapshot path ever reaches for the realtime bus."""

    def subscribe(self, tenant_id: UUID) -> AsyncIterator[PresenceFact]:
        raise AssertionError("the snapshot must not touch the realtime bus")


class _RaisingPresencePublisher:
    """Violates the best-effort port contract; the read path is unaffected."""

    async def publish(self, fact: PresenceFact) -> None:
        raise RuntimeError("bus unavailable")


async def _seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    tenant: UUID = TENANT_A,
    employee_id: UUID = ACTOR,
) -> None:
    await PostgresEmployeeRepository(session).add(
        tenant,
        employee_id,
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


async def test_snapshot_matches_the_attendance_board_at_one_instant(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 1: same projection, same instant — counts, rows and activity."""
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        grace = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Grace",
                    last_name="Hopper",
                    email="grace@example.test",
                ),
            )
        ).json()
        absent = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Ada",
                    last_name="Absent",
                    email="ada.absent@example.test",
                ),
            )
        ).json()
    await _seed_employee(session, refs)

    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    monkeypatch.setattr(realtime_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        shift = await admin.post(
            "/api/v1/shifts",
            json={
                "name": "Always past",
                "start_time": "00:00",
                "end_time": "23:59",
                "grace_minutes": 0,
            },
        )
        assert shift.status_code == 201

    async with client_factory() as client:
        actor_record = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory(identity(employee_id=UUID(grace["id"]))) as other:
        response = await other.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "remote"}
        )
        assert response.status_code == 201

    board_date = date.fromisoformat(actor_record["workday"])
    async with client_factory() as admin:
        board = (
            await admin.get(
                "/api/v1/attendance/board", params={"date": board_date.isoformat()}
            )
        ).json()
        response = await admin.get("/api/v1/realtime/board")

    assert response.status_code == 200
    snapshot = response.json()
    assert set(snapshot) == PINNED_SNAPSHOT_KEYS
    assert snapshot["date"] == board_date.isoformat()  # the injected UTC date
    assert set(snapshot["counts"]) == PINNED_BOARD_COUNT_KEYS
    assert snapshot["counts"] == board["counts"]
    assert snapshot["activity"] == board["activity"]
    assert snapshot["counts"] == {
        "present": 1,
        "remote": 1,
        "late": 2,  # the 00:00 + 0-grace shift: both check-ins are past it
        "on_leave": 0,
        "absent": 1,  # Ada has no record and is past start + grace
        "checked_out": 0,
    }

    rows = {row["employee_id"]: row for row in snapshot["presence"]}
    assert set(rows) == {str(ACTOR), grace["id"], absent["id"]}
    for row in rows.values():
        assert set(row) == PINNED_PRESENCE_ROW_KEYS
    actor_row = rows[str(ACTOR)]
    assert actor_row["status"] == "present"
    assert actor_row["late"] is True
    assert actor_row["minutes_late"] == 660
    assert actor_row["attendance_id"] == actor_record["id"]
    grace_row = rows[grace["id"]]
    assert grace_row["status"] == "remote"
    assert grace_row["late"] is True  # `late` is orthogonal to presence (ADR 0009)
    absent_row = rows[absent["id"]]
    assert absent_row["status"] == "absent"
    assert absent_row["since"] is None
    assert absent_row["attendance_id"] is None

    # The REST body is the shared projection verbatim (rows are unordered).
    service = AttendanceService(
        repo=PostgresAttendanceRepository(session),
        shifts=PostgresShiftRepository(session),
        audit=PostgresAuditWriter(session),
        presence=RedisPresencePublisher(get_redis()),
        # Board reads append nothing; the port is only required by the ctor.
        outbox=RecordingEventOutbox(),
    )
    detail = await service.board_detail(identity(), board_date, CLOCK)
    assert [row.model_dump(mode="json") for row in detail.rows] == snapshot["presence"]


async def test_manager_snapshot_is_scoped_to_self_and_direct_reports(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)  # the manager's own employee link

    async with client_factory() as admin:
        report = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(refs, manager_id=str(ACTOR)),
            )
        ).json()
        outsider = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Grace",
                    last_name="Hopper",
                    email="grace@example.test",
                ),
            )
        ).json()

    async with client_factory(identity(employee_id=UUID(report["id"]))) as them:
        report_record = (
            await them.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory(identity(employee_id=UUID(outsider["id"]))) as them:
        await them.post("/api/v1/attendance/check-ins", json={"work_mode": "onsite"})

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        response = await manager.get("/api/v1/realtime/board")

    assert response.status_code == 200
    snapshot = response.json()
    assert [row["employee_id"] for row in snapshot["presence"]] == [report["id"]]
    assert snapshot["counts"]["present"] == 1
    assert [entry["employee_id"] for entry in snapshot["activity"]] == [report["id"]]
    assert snapshot["presence"][0]["attendance_id"] == report_record["id"]


async def test_unlinked_manager_sees_an_empty_board(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)
    async with client_factory() as client:
        await client.post("/api/v1/attendance/check-ins", json={"work_mode": "onsite"})

    async with client_factory(identity(role=Role.MANAGER, employee_id=None)) as manager:
        response = await manager.get("/api/v1/realtime/board")

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["counts"] == ZERO_COUNTS
    assert snapshot["presence"] == []
    assert snapshot["activity"] == []


@pytest.mark.parametrize("role", ["employee", "finance"])
async def test_snapshot_denied_for_employee_and_finance(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity(role=Role(role))) as client:
        response = await client.get("/api/v1/realtime/board")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_snapshot_without_a_session_is_unauthenticated(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(None) as client:
        response = await client.get("/api/v1/realtime/board")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_tenants_only_ever_see_their_own_board(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    monkeypatch.setattr(realtime_api, "_now", lambda: CLOCK)

    async with client_factory() as tenant_a:
        refs_a = await create_org_refs(tenant_a, suffix=" A")
    await _seed_employee(session, refs_a)

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        refs_b = await create_org_refs(tenant_b, suffix=" B")
    await _seed_employee(session, refs_b, tenant=TENANT_B, employee_id=B_ACTOR)

    async with client_factory() as tenant_a:
        await tenant_a.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        snapshot_a = (await tenant_a.get("/api/v1/realtime/board")).json()
    async with client_factory(
        identity(tenant=TENANT_B, employee_id=B_ACTOR)
    ) as tenant_b:
        await tenant_b.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        snapshot_b = (await tenant_b.get("/api/v1/realtime/board")).json()

    assert [row["employee_id"] for row in snapshot_a["presence"]] == [str(ACTOR)]
    assert snapshot_a["counts"]["present"] == 1
    assert [row["employee_id"] for row in snapshot_b["presence"]] == [str(B_ACTOR)]
    assert snapshot_b["counts"]["present"] == 1
    assert str(B_ACTOR) not in {row["employee_id"] for row in snapshot_a["presence"]}
    assert str(ACTOR) not in {row["employee_id"] for row in snapshot_b["presence"]}


async def test_board_and_snapshot_stay_consistent_after_a_re_check_in(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One person, one contribution — the wall's first delta cannot contradict it."""
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    monkeypatch.setattr(realtime_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory() as client:
        first = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
        closed = await client.post(
            f"/api/v1/attendance/records/{first['id']}/check-out", json={}
        )
        assert closed.status_code == 200
        second = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()

    async with client_factory() as admin:
        board = (
            await admin.get(
                "/api/v1/attendance/board", params={"date": second["workday"]}
            )
        ).json()
        snapshot = (await admin.get("/api/v1/realtime/board")).json()

    assert snapshot["counts"] == board["counts"]
    assert snapshot["counts"] == {
        "present": 1,  # the superseded closed record is not a second contribution
        "remote": 0,
        "late": 0,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }
    rows = {row["employee_id"]: row for row in snapshot["presence"]}
    assert rows[str(ACTOR)]["attendance_id"] == second["id"]  # newest record wins
    assert len(snapshot["activity"]) == 3  # two check-ins + the check-out


async def test_snapshot_is_served_with_a_dead_bus(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 6: the REST snapshot is PostgreSQL-only; a dead bus changes nothing."""
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    monkeypatch.setattr(realtime_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)
    async with client_factory() as client:
        record = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()

    def _dead_service() -> RealtimeService:
        return RealtimeService(
            attendance=AttendanceService(
                repo=PostgresAttendanceRepository(session),
                shifts=PostgresShiftRepository(session),
                audit=PostgresAuditWriter(session),
                presence=_RaisingPresencePublisher(),
                outbox=RecordingEventOutbox(),
            ),
            stream=_ExplodingStream(),
        )

    app.dependency_overrides[get_realtime_service] = _dead_service
    try:
        async with client_factory() as client:
            response = await client.get("/api/v1/realtime/board")
    finally:
        app.dependency_overrides.pop(get_realtime_service, None)

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["counts"]["present"] == 1
    assert snapshot["presence"][0]["attendance_id"] == record["id"]
