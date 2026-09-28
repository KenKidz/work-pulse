"""HTTP failure cases — every pinned 4xx code, over the wire (AC4–AC6 and friends)."""

from datetime import date
from uuid import UUID, uuid4

from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity_headers,
)

DEFAULT_SHIFT = {
    "name": "Default",
    "start_time": "09:00",
    "end_time": "18:00",
    "grace_minutes": 10,
}


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code


async def _seed_actor(session: AsyncSession, refs: tuple[str, str, str]) -> None:
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


async def _created_employee(client, refs: tuple[str, str, str]) -> dict:
    response = await client.post("/api/v1/employees", json=employee_payload(refs))
    assert response.status_code == 201
    return response.json()


async def test_double_check_in_conflicts(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        first = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert first.status_code == 201
        second = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )

    _assert_error(second, 409, "attendance_already_checked_in")  # AC4


async def test_check_out_of_closed_record_conflicts(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        opened = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
        first = await client.post(
            f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
        )

    _assert_error(second, 409, "attendance_not_open")  # AC5


async def test_check_out_of_unknown_record_is_404(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        response = await client.post(
            f"/api/v1/attendance/records/{uuid4()}/check-out", json={}
        )

    _assert_error(response, 404, "attendance_record_not_found")


async def test_check_out_of_someone_elses_record_is_404(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        other = await _created_employee(client, refs)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        opened = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory(identity_headers(actor=UUID(other["id"]))) as them:
        response = await them.post(
            f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
        )

    # Never 403 — the record's existence is not leaked (T1).
    _assert_error(response, 404, "attendance_record_not_found")


async def test_terminated_employee_cannot_check_in_or_out(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        open_at_termination = await _created_employee(client, refs)
        terminated = (
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
        response = await client.post(
            f"/api/v1/employees/{terminated['id']}/termination",
            json={"effective_date": "2026-09-01", "reason": "offboarding"},
        )
        assert response.status_code == 200

    async with client_factory(identity_headers(actor=UUID(terminated["id"]))) as gone:
        never_checked_in = await gone.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )

    async with client_factory(
        identity_headers(actor=UUID(open_at_termination["id"]))
    ) as leaving:
        opened = (
            await leaving.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory() as admin:
        response = await admin.post(
            f"/api/v1/employees/{open_at_termination['id']}/termination",
            json={"effective_date": "2026-09-01", "reason": "offboarding"},
        )
        assert response.status_code == 200
    async with client_factory(
        identity_headers(actor=UUID(open_at_termination["id"]))
    ) as leaving:
        check_out = await leaving.post(
            f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
        )

    _assert_error(never_checked_in, 409, "employee_not_active")  # AC6
    _assert_error(check_out, 409, "employee_not_active")  # Risk 4: stays open


async def test_invalid_work_mode_is_400(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "hybrid"}
        )

    _assert_error(response, 400, "invalid_work_mode")


async def test_check_in_rejects_client_timestamps(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins",
            json={"work_mode": "onsite", "checked_in_at": "2026-09-01T08:00:00Z"},
        )

    # `additionalProperties: false` — server time is authoritative (Decision #3).
    _assert_error(response, 422, "validation_error")


async def test_check_in_without_employee_record_is_404(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )

    _assert_error(response, 404, "employee_not_found")


async def test_shift_scope_taken_conflicts(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        first = await client.post("/api/v1/shifts", json=DEFAULT_SHIFT)
        assert first.status_code == 201
        second_default = await client.post("/api/v1/shifts", json=DEFAULT_SHIFT)
        scoped = await client.post(
            "/api/v1/shifts", json={**DEFAULT_SHIFT, "location_id": refs[2]}
        )
        assert scoped.status_code == 201
        second_scoped = await client.post(
            "/api/v1/shifts",
            json={**DEFAULT_SHIFT, "name": "Other", "location_id": refs[2]},
        )

    _assert_error(second_default, 409, "shift_scope_taken")
    _assert_error(second_scoped, 409, "shift_scope_taken")


async def test_shift_time_invalid_is_422(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/shifts", json={**DEFAULT_SHIFT, "end_time": "09:00"}
        )
        assert response.status_code == 422
        created = (await client.post("/api/v1/shifts", json=DEFAULT_SHIFT)).json()
        # A patch is validated against the merged window, not just the payload.
        patched = await client.patch(
            f"/api/v1/shifts/{created['id']}", json={"start_time": "19:00"}
        )

    _assert_error(response, 422, "shift_time_invalid")
    _assert_error(patched, 422, "shift_time_invalid")


async def test_shift_location_must_exist(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        created = (await client.post("/api/v1/shifts", json=DEFAULT_SHIFT)).json()
        unknown = str(uuid4())
        bad_create = await client.post(
            "/api/v1/shifts", json={**DEFAULT_SHIFT, "location_id": unknown}
        )
        bad_update = await client.patch(
            f"/api/v1/shifts/{created['id']}", json={"location_id": unknown}
        )

    _assert_error(bad_create, 404, "location_not_found")
    _assert_error(bad_update, 404, "location_not_found")


async def test_update_unknown_shift_is_404(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        response = await client.patch(
            f"/api/v1/shifts/{uuid4()}", json={"name": "Ghost"}
        )

    _assert_error(response, 404, "shift_not_found")
