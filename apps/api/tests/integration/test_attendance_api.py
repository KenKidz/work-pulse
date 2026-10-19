"""HTTP integration tests for /api/v1/attendance — AC1, AC2, board, shifts CRUD."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.domains.attendance.service import AttendanceService
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.repositories.attendance import (
    PostgresAttendanceRepository,
    PostgresShiftRepository,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)
from tests.unit.outbox.fakes import RecordingEventOutbox

PINNED_RECORD_KEYS = {
    "id",
    "tenant_id",
    "employee_id",
    "workday",
    "checked_in_at",
    "checked_out_at",
    "work_mode",
    "late",
    "shift_id",
    "check_in_note",
    "check_out_note",
    "duration_minutes",
    "created_at",
}

PINNED_SHIFT_KEYS = {
    "id",
    "tenant_id",
    "name",
    "start_time",
    "end_time",
    "grace_minutes",
    "location_id",
    "created_at",
    "updated_at",
}

PINNED_BOARD_COUNT_KEYS = {
    "present",
    "remote",
    "late",
    "on_leave",
    "absent",
    "checked_out",
}


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


async def _refs_with_timezone(
    client: AsyncClient, timezone: str
) -> tuple[str, str, str]:
    department = (
        await client.post("/api/v1/departments", json={"name": "Engineering"})
    ).json()
    position = (
        await client.post("/api/v1/positions", json={"title": "Engineer"})
    ).json()
    location = (
        await client.post(
            "/api/v1/locations", json={"name": "HCMC", "timezone": timezone}
        )
    ).json()
    return department["id"], position["id"], location["id"]


def _next_day(workday: str) -> str:
    return (date.fromisoformat(workday) + timedelta(days=1)).isoformat()


async def test_check_in_returns_201_with_pinned_body_and_location_workday(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await _refs_with_timezone(client, "Asia/Ho_Chi_Minh")
    await _seed_actor(session, refs)

    hcmc = ZoneInfo("Asia/Ho_Chi_Minh")
    before = datetime.now(UTC).astimezone(hcmc).date()
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins",
            json={"work_mode": "onsite", "check_in_note": "morning"},
        )
    after = datetime.now(UTC).astimezone(hcmc).date()

    assert response.status_code == 201  # AC1
    body = response.json()
    assert set(body) == PINNED_RECORD_KEYS  # exact pinned shape
    assert body["tenant_id"] == str(TENANT_A)
    assert body["employee_id"] == str(ACTOR)
    assert body["workday"] in {before.isoformat(), after.isoformat()}  # AC1
    assert body["checked_out_at"] is None
    assert body["duration_minutes"] is None
    assert body["work_mode"] == "onsite"
    assert body["late"] is False  # no shift configured → never late
    assert body["shift_id"] is None
    assert body["check_in_note"] == "morning"
    assert body["check_out_note"] is None
    assert body["checked_in_at"].endswith("Z")  # date-time on the wire


async def test_check_out_closes_record_and_reports_duration(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        opened = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "remote"}
            )
        ).json()
        response = await client.post(
            f"/api/v1/attendance/records/{opened['id']}/check-out",
            json={"check_out_note": "evening"},
        )

    assert response.status_code == 200  # AC2
    body = response.json()
    assert set(body) == PINNED_RECORD_KEYS
    assert body["id"] == opened["id"]
    assert body["workday"] == opened["workday"]  # never recomputed
    assert body["checked_out_at"] is not None
    assert body["duration_minutes"] is not None
    assert body["duration_minutes"] >= 0
    assert body["check_out_note"] == "evening"


async def test_records_list_filters_and_stable_pagination(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
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

    async with client_factory() as client:
        actor_record = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory(identity(employee_id=UUID(grace["id"]))) as other:
        grace_record = (
            await other.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "remote"}
            )
        ).json()

    days = sorted({actor_record["workday"], grace_record["workday"]})
    async with client_factory() as admin:
        listed = (await admin.get("/api/v1/attendance/records")).json()
        assert listed["page"] == {
            "page": 1,
            "page_size": 20,
            "total_items": 2,
            "total_pages": 1,
        }
        assert set(listed["items"][0]) == PINNED_RECORD_KEYS
        # Pinned order: checked_in_at desc — the second check-in leads.
        assert [item["id"] for item in listed["items"]] == [
            grace_record["id"],
            actor_record["id"],
        ]

        by_employee = (
            await admin.get(
                "/api/v1/attendance/records",
                params={"employee_id": actor_record["employee_id"]},
            )
        ).json()
        assert [item["id"] for item in by_employee["items"]] == [actor_record["id"]]

        in_range = (
            await admin.get(
                "/api/v1/attendance/records",
                params={"from": days[0], "to": days[-1]},
            )
        ).json()
        assert in_range["page"]["total_items"] == 2
        after_range = (
            await admin.get(
                "/api/v1/attendance/records", params={"from": _next_day(days[-1])}
            )
        ).json()
        assert after_range["page"]["total_items"] == 0

        never_late = (
            await admin.get("/api/v1/attendance/records", params={"late": "false"})
        ).json()
        assert never_late["page"]["total_items"] == 2
        late_only = (
            await admin.get("/api/v1/attendance/records", params={"late": "true"})
        ).json()
        assert late_only["page"]["total_items"] == 0

        page1 = (
            await admin.get(
                "/api/v1/attendance/records", params={"page": 1, "page_size": 1}
            )
        ).json()
        page2 = (
            await admin.get(
                "/api/v1/attendance/records", params={"page": 2, "page_size": 1}
            )
        ).json()
        assert [item["id"] for item in page1["items"]] == [grace_record["id"]]
        assert [item["id"] for item in page2["items"]] == [actor_record["id"]]
        assert page2["page"]["total_pages"] == 2

        clamped = (
            await admin.get("/api/v1/attendance/records", params={"page_size": 1000})
        ).json()
        assert clamped["page"]["page_size"] == 100


async def test_board_counts_independent_and_activity_newest_first(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    # Injected clock: all three mutations provably share one UTC workday and
    # have deterministic newest-first ordering (no wall-clock assertions).
    clock = {"now": datetime(2026, 9, 24, 11, 0, tzinfo=UTC)}
    monkeypatch.setattr(attendance_api, "_now", lambda: clock["now"])

    async with client_factory() as client:
        actor_record = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    clock["now"] = datetime(2026, 9, 24, 11, 5, tzinfo=UTC)
    async with client_factory(identity(employee_id=UUID(grace["id"]))) as other:
        await other.post("/api/v1/attendance/check-ins", json={"work_mode": "remote"})

    board_date = actor_record["workday"]
    clock["now"] = datetime(2026, 9, 24, 11, 30, tzinfo=UTC)
    async with client_factory() as admin:
        open_board = (
            await admin.get("/api/v1/attendance/board", params={"date": board_date})
        ).json()
        closed = await admin.post(
            f"/api/v1/attendance/records/{actor_record['id']}/check-out", json={}
        )
        assert closed.status_code == 200
        after_board = (
            await admin.get("/api/v1/attendance/board", params={"date": board_date})
        ).json()

    assert open_board["date"] == board_date
    assert set(open_board["counts"]) == PINNED_BOARD_COUNT_KEYS
    # Counts are independent, not a partition — pins the T1 semantics.
    assert open_board["counts"] == {
        "present": 1,
        "remote": 1,
        "late": 0,
        "on_leave": 0,
        "absent": 0,  # no shift configured → nobody is expected
        "checked_out": 0,
    }
    assert set(open_board["activity"][0]) == {
        "occurred_at",
        "employee_id",
        "action",
    }
    assert [entry["action"] for entry in open_board["activity"]] == [
        "checked_in",
        "checked_in",
    ]

    assert after_board["counts"] == {
        "present": 0,
        "remote": 1,
        "late": 0,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 1,
    }
    # Activity newest first; the actor's check-out leads.
    assert [entry["action"] for entry in after_board["activity"]] == [
        "checked_out",
        "checked_in",
        "checked_in",
    ]
    assert after_board["activity"][0]["employee_id"] == str(ACTOR)


async def test_board_detail_matches_the_wire_board_and_counts_recount_rows(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3 projection invariants: one code path, counts == recall of rows.

    The realtime snapshot maps `board_detail`; the REST board maps the same
    detail — so their counts/activity must be identical, `absent` must equal
    the number of absent rows, and rows must cover every recorded employee plus
    every currently-absent employee (ADR 0009 semantics).
    """
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
        absent = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Ada",
                    last_name="Absent",
                    email="ada.absent@example.test",
                ),
            )
        ).json()
    await _seed_actor(session, refs)

    clock = {"now": datetime(2026, 9, 24, 11, 0, tzinfo=UTC)}
    monkeypatch.setattr(attendance_api, "_now", lambda: clock["now"])

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
        grace_record = (
            await other.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "remote"}
            )
        ).json()

    board_date = date.fromisoformat(actor_record["workday"])
    async with client_factory() as admin:
        wire = (
            await admin.get(
                "/api/v1/attendance/board", params={"date": board_date.isoformat()}
            )
        ).json()

    service = AttendanceService(
        repo=PostgresAttendanceRepository(session),
        shifts=PostgresShiftRepository(session),
        audit=PostgresAuditWriter(session),
        # Board reads append nothing; the port is only required by the ctor.
        outbox=RecordingEventOutbox(),
    )
    detail = await service.board_detail(identity(), board_date, clock["now"])

    # Never disagree with the wire board: same projection, same instant.
    assert detail.date == board_date
    assert detail.counts.model_dump() == wire["counts"]
    assert [entry.model_dump(mode="json") for entry in detail.activity] == (
        wire["activity"]
    )

    # counts == recount(rows) — overlapping ADR 0009 counts, one record per
    # employee in this fixture.
    assert detail.counts.model_dump() == {
        "present": sum(row.status == "present" for row in detail.rows),
        "remote": sum(row.status == "remote" for row in detail.rows),
        "late": sum(row.late for row in detail.rows),
        "on_leave": sum(row.status == "on_leave" for row in detail.rows),
        "absent": sum(row.status == "absent" for row in detail.rows),
        "checked_out": sum(row.status == "checked_out" for row in detail.rows),
    }

    # Rows cover every employee with a record for the date + every absent one.
    rows = {row.employee_id: row for row in detail.rows}
    assert set(rows) == {
        UUID(actor_record["employee_id"]),
        UUID(grace_record["employee_id"]),
        UUID(absent["id"]),
    }
    actor_row = rows[UUID(actor_record["employee_id"])]
    assert actor_row.status == "present"
    assert actor_row.late is True
    assert actor_row.minutes_late == 660  # 11:00 past a 00:00 + 0-grace shift
    assert actor_row.since == clock["now"]
    assert actor_row.attendance_id == UUID(actor_record["id"])
    grace_row = rows[UUID(grace_record["employee_id"])]
    assert grace_row.status == "remote"
    absent_row = rows[UUID(absent["id"])]
    assert absent_row.status == "absent"
    assert absent_row.since is None
    assert absent_row.attendance_id is None

    # A check-out flips the row and the late minutes survive the close.
    clock["now"] = datetime(2026, 9, 24, 11, 30, tzinfo=UTC)
    async with client_factory() as client:
        closed = await client.post(
            f"/api/v1/attendance/records/{actor_record['id']}/check-out", json={}
        )
        assert closed.status_code == 200
    after = await service.board_detail(identity(), board_date, clock["now"])
    closed_row = {row.employee_id: row for row in after.rows}[
        UUID(actor_record["employee_id"])
    ]
    assert closed_row.status == "checked_out"
    assert closed_row.since == clock["now"]
    assert closed_row.late is True
    assert closed_row.minutes_late == 660


async def test_board_defaults_to_server_utc_date(
    client_factory: ClientFactory,
) -> None:
    before = datetime.now(UTC).date()
    async with client_factory() as admin:
        board = await admin.get("/api/v1/attendance/board")
    after = datetime.now(UTC).date()

    assert board.status_code == 200
    # No tenant-level timezone exists (T1) — the default is the server UTC date.
    assert board.json()["date"] in {before.isoformat(), after.isoformat()}


async def test_shift_makes_the_check_in_late_and_the_board_counts_it(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as admin:
        # A shift whose window can never lie in the future of "now".
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
        record = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    assert record["late"] is True  # T4 lateness judgement, wired end to end
    assert record["shift_id"] == shift.json()["id"]

    async with client_factory() as admin:
        board = (
            await admin.get(
                "/api/v1/attendance/board", params={"date": record["workday"]}
            )
        ).json()
        late_only = (
            await admin.get(
                "/api/v1/attendance/records",
                params={
                    "late": "true",
                    "from": record["workday"],
                    "to": record["workday"],
                },
            )
        ).json()

    # `late` is orthogonal to presence: the same person is present and late.
    assert board["counts"] == {
        "present": 1,
        "remote": 0,
        "late": 1,
        "on_leave": 0,
        "absent": 0,  # the employee has a record for the day
        "checked_out": 0,
    }
    assert [item["id"] for item in late_only["items"]] == [record["id"]]


async def test_shifts_crud_happy_path(client_factory: ClientFactory) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        default = await admin.post(
            "/api/v1/shifts",
            json={
                "name": "Default",
                "start_time": "09:00",
                "end_time": "18:00",
                "grace_minutes": 10,
            },
        )
        assert default.status_code == 201
        body = default.json()
        assert set(body) == PINNED_SHIFT_KEYS
        assert body["tenant_id"] == str(TENANT_A)
        assert body["name"] == "Default"
        assert body["start_time"] == "09:00"  # HH:MM on the wire, no seconds
        assert body["end_time"] == "18:00"
        assert body["grace_minutes"] == 10
        assert body["location_id"] is None

        scoped = await admin.post(
            "/api/v1/shifts",
            json={
                "name": "HCMC",
                "start_time": "08:30",
                "end_time": "17:30",
                "grace_minutes": 5,
                "location_id": refs[2],
            },
        )
        assert scoped.status_code == 201
        assert scoped.json()["location_id"] == refs[2]

        listed = await admin.get("/api/v1/shifts")
        assert listed.status_code == 200
        shifts = listed.json()
        assert isinstance(shifts, list)  # bare unpaged array
        # Pinned order: location-scoped first, tenant default (null) last.
        assert [shift["id"] for shift in shifts] == [
            scoped.json()["id"],
            body["id"],
        ]

        patched = await admin.patch(
            f"/api/v1/shifts/{body['id']}",
            json={"name": "Default v2", "grace_minutes": 15},
        )
        assert patched.status_code == 200
        updated = patched.json()
        assert set(updated) == PINNED_SHIFT_KEYS
        assert updated["name"] == "Default v2"
        assert updated["grace_minutes"] == 15
        assert updated["start_time"] == "09:00"  # untouched
        assert updated["location_id"] is None

        noop = await admin.patch(f"/api/v1/shifts/{body['id']}", json={})
        assert noop.status_code == 200
        assert noop.json()["name"] == "Default v2"
