"""AC7 — tenant isolation: B never sees or touches A's attendance data."""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    identity_headers,
)


async def _seed_tenant_a_actor(
    session: AsyncSession, refs: tuple[str, str, str]
) -> None:
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


async def test_tenant_b_stays_isolated_from_tenant_a_attendance(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as tenant_a:
        refs = await create_org_refs(tenant_a)
    await _seed_tenant_a_actor(session, refs)

    async with client_factory() as tenant_a:
        record = (
            await tenant_a.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
        shift = (
            await tenant_a.post(
                "/api/v1/shifts",
                json={
                    "name": "Default",
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "grace_minutes": 10,
                },
            )
        ).json()

    async with client_factory(identity_headers(tenant=TENANT_B)) as tenant_b:
        board = await tenant_b.get(
            "/api/v1/attendance/board", params={"date": record["workday"]}
        )
        listed = await tenant_b.get("/api/v1/attendance/records")
        checkout = await tenant_b.post(
            f"/api/v1/attendance/records/{record['id']}/check-out", json={}
        )
        shifts = await tenant_b.get("/api/v1/shifts")
        foreign_shift = await tenant_b.patch(
            f"/api/v1/shifts/{shift['id']}", json={"name": "Sneaky"}
        )
        foreign_location = await tenant_b.post(
            "/api/v1/shifts",
            json={
                "name": "Sneaky",
                "start_time": "09:00",
                "end_time": "18:00",
                "grace_minutes": 0,
                "location_id": refs[2],  # tenant A's location
            },
        )
        foreign_check_in = await tenant_b.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        own_shift = await tenant_b.post(
            "/api/v1/shifts",
            json={
                "name": "B shift",
                "start_time": "09:00",
                "end_time": "18:00",
                "grace_minutes": 0,
            },
        )
        foreign_update = await tenant_b.patch(
            f"/api/v1/shifts/{own_shift.json()['id']}",
            json={"location_id": refs[2]},  # tenant A's location
        )
        after_foreign_update = await tenant_b.get("/api/v1/shifts")

    assert board.status_code == 200
    assert board.json()["counts"] == {
        "present": 0,
        "remote": 0,
        "late": 0,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }
    assert board.json()["activity"] == []
    assert listed.json()["page"]["total_items"] == 0
    assert checkout.status_code == 404
    assert checkout.json()["error"]["code"] == "attendance_record_not_found"
    assert shifts.json() == []  # B sees only its own (empty) shifts
    assert foreign_shift.status_code == 404
    assert foreign_shift.json()["error"]["code"] == "shift_not_found"
    assert foreign_location.status_code == 404
    assert foreign_location.json()["error"]["code"] == "location_not_found"
    assert foreign_update.status_code == 404
    assert foreign_update.json()["error"]["code"] == "location_not_found"
    # The rejected patch never mutated B's shift.
    assert after_foreign_update.json()[0]["location_id"] is None
    # B's actor has no employee row in B — A's employee is never resolved.
    assert foreign_check_in.status_code == 404
    assert foreign_check_in.json()["error"]["code"] == "employee_not_found"
