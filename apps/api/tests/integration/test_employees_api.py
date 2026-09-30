"""HTTP integration tests for /api/v1/employees — AC1, AC2, AC3, pinned contract."""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)

PINNED_EMPLOYEE_KEYS = {
    "id",
    "tenant_id",
    "employee_code",
    "first_name",
    "last_name",
    "email",
    "phone",
    "department_id",
    "position_id",
    "location_id",
    "manager_id",
    "employment_type",
    "status",
    "hired_at",
    "terminated_at",
    "created_at",
    "updated_at",
}

PINNED_EMPLOYMENT_KEYS = {
    "id",
    "tenant_id",
    "employee_id",
    "department_id",
    "position_id",
    "location_id",
    "employment_type",
    "effective_from",
    "effective_to",
    "created_at",
}


async def test_create_returns_201_with_exact_body_and_initial_period(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        response = await client.post("/api/v1/employees", json=employee_payload(refs))
        assert response.status_code == 201  # AC1
        body = response.json()
        assert set(body) == PINNED_EMPLOYEE_KEYS  # exact pinned shape
        assert body["employee_code"] == "EMP-00001"  # auto code, per tenant
        assert body["tenant_id"] == str(TENANT_A)
        assert body["status"] == "active"
        assert body["hired_at"] == "2026-01-05"
        assert body["terminated_at"] is None
        assert body["manager_id"] is None
        assert body["employment_type"] == "full_time"

        detail = (await client.get(f"/api/v1/employees/{body['id']}")).json()
        assert set(detail) == PINNED_EMPLOYEE_KEYS | {
            "current_employment",
            "employments",
        }
        current = detail["current_employment"]
        assert current is not None
        assert set(current) == PINNED_EMPLOYMENT_KEYS
        assert current["effective_from"] == "2026-01-05"  # initial period opens at hire
        assert current["effective_to"] is None  # open
        assert current["department_id"] == refs[0]
        assert [period["id"] for period in detail["employments"]] == [current["id"]]

        listed = (
            await client.get(f"/api/v1/employees/{body['id']}/employments")
        ).json()
        assert [period["id"] for period in listed] == [current["id"]]


async def test_create_auto_codes_increment_and_are_overridable(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        first = (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        second = (
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
        supplied = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Alan",
                    last_name="Turing",
                    email="alan@example.test",
                    employee_code="HR-42",
                ),
            )
        ).json()

    assert first["employee_code"] == "EMP-00001"
    assert second["employee_code"] == "EMP-00002"
    assert supplied["employee_code"] == "HR-42"


async def test_list_filters_pagination_and_stable_order(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs_a = await create_org_refs(client)
        refs_b = await create_org_refs(client, suffix="-B")
        ada = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs_a,
                    first_name="Ada",
                    last_name="Chen",
                    email="ada.chen@example.test",
                ),
            )
        ).json()
        bo = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs_a,
                    first_name="Bo",
                    last_name="Chen",
                    email="bo.chen@example.test",
                    manager_id=ada["id"],
                ),
            )
        ).json()
        cy = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs_b,
                    first_name="Cy",
                    last_name="Diaz",
                    email="cy.diaz@example.test",
                ),
            )
        ).json()
        terminated = await client.post(
            f"/api/v1/employees/{cy['id']}/termination",
            json={"effective_date": "2026-06-01", "reason": "left"},
        )
        assert terminated.status_code == 200

        # AC2: default order is last_name, first_name ascending.
        default = (await client.get("/api/v1/employees")).json()
        assert default["page"] == {
            "page": 1,
            "page_size": 20,
            "total_items": 3,
            "total_pages": 1,
        }
        assert [item["id"] for item in default["items"]] == [
            ada["id"],
            bo["id"],
            cy["id"],
        ]
        assert set(default["items"][0]) == PINNED_EMPLOYEE_KEYS - {
            "tenant_id",
            "phone",
            "created_at",
            "updated_at",
        }  # EmployeeSummary shape

        # AC2: filters compose.
        by_q = (await client.get("/api/v1/employees", params={"q": "diaz"})).json()
        assert [item["id"] for item in by_q["items"]] == [cy["id"]]
        by_department = (
            await client.get("/api/v1/employees", params={"department_id": refs_a[0]})
        ).json()
        assert {item["id"] for item in by_department["items"]} == {ada["id"], bo["id"]}
        by_position_b = (
            await client.get("/api/v1/employees", params={"position_id": refs_b[1]})
        ).json()
        assert [item["id"] for item in by_position_b["items"]] == [cy["id"]]
        by_location_a = (
            await client.get("/api/v1/employees", params={"location_id": refs_a[2]})
        ).json()
        assert {item["id"] for item in by_location_a["items"]} == {ada["id"], bo["id"]}
        by_manager = (
            await client.get("/api/v1/employees", params={"manager_id": ada["id"]})
        ).json()
        assert [item["id"] for item in by_manager["items"]] == [bo["id"]]
        by_status = (
            await client.get("/api/v1/employees", params={"status": "terminated"})
        ).json()
        assert [item["id"] for item in by_status["items"]] == [cy["id"]]
        on_leave = (
            await client.get("/api/v1/employees", params={"status": "on_leave"})
        ).json()
        assert on_leave["page"]["total_items"] == 0  # reserved for Attendance

        # AC2: pagination stays stable across pages.
        page1 = (
            await client.get("/api/v1/employees", params={"page": 1, "page_size": 2})
        ).json()
        page2 = (
            await client.get("/api/v1/employees", params={"page": 2, "page_size": 2})
        ).json()
        assert [item["id"] for item in page1["items"]] == [ada["id"], bo["id"]]
        assert [item["id"] for item in page2["items"]] == [cy["id"]]
        assert page2["page"]["total_pages"] == 2

        clamped = (
            await client.get("/api/v1/employees", params={"page_size": 1000})
        ).json()
        assert clamped["page"]["page_size"] == 100
        page_zero = (await client.get("/api/v1/employees", params={"page": 0})).json()
        assert page_zero["page"]["page"] == 1


async def test_me_resolves_the_actor_record(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    # The actor's own employee row must carry the actor id — seed it directly.
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

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        mine = await employee.get("/api/v1/employees/me")
    assert mine.status_code == 200
    assert mine.json()["id"] == str(ACTOR)

    missing_actor = UUID("00000000-0000-4000-8000-000000000003")
    async with client_factory(identity(employee_id=missing_actor)) as other:
        missing = await other.get("/api/v1/employees/me")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "employee_not_found"


async def test_patch_profile_fields_and_empty_patch_is_noop(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).json()

        patched = await client.patch(
            f"/api/v1/employees/{created['id']}",
            json={"first_name": "Augusta", "phone": "+84-000"},
        )
        assert patched.status_code == 200
        assert patched.json()["first_name"] == "Augusta"
        assert patched.json()["phone"] == "+84-000"
        assert patched.json()["last_name"] == "Lovelace"  # untouched

        cleared = await client.patch(
            f"/api/v1/employees/{created['id']}", json={"phone": None}
        )
        assert cleared.status_code == 200
        assert cleared.json()["phone"] is None  # explicit null clears

        noop = await client.patch(f"/api/v1/employees/{created['id']}", json={})
        assert noop.status_code == 200
        assert noop.json()["first_name"] == "Augusta"


async def test_transfer_appends_period_and_keeps_history(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs_a = await create_org_refs(client)
        refs_b = await create_org_refs(client, suffix="-B")
        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs_a))
        ).json()

        # AC3: a transfer closes the current period and appends a new one.
        response = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={
                "department_id": refs_b[0],
                "effective_from": "2026-06-01",
                "reason": "reorg",
            },
        )
        assert response.status_code == 200
        period = response.json()
        assert set(period) == PINNED_EMPLOYMENT_KEYS
        assert period["department_id"] == refs_b[0]
        assert period["position_id"] == refs_a[1]  # omitted → inherited
        assert period["location_id"] == refs_a[2]  # omitted → inherited
        assert period["effective_from"] == "2026-06-01"
        assert period["effective_to"] is None

        detail = (await client.get(f"/api/v1/employees/{created['id']}")).json()
        assert detail["department_id"] == refs_b[0]
        assert [p["effective_from"] for p in detail["employments"]] == [
            "2026-06-01",
            "2026-01-05",
        ]
        assert detail["current_employment"]["id"] == period["id"]
        assert detail["employments"][1]["effective_to"] == "2026-06-01"  # closed

        history = (
            await client.get(f"/api/v1/employees/{created['id']}/employments")
        ).json()
        assert [p["id"] for p in history] == [p["id"] for p in detail["employments"]]


async def test_termination_closes_period_and_unassigns_reports(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        manager = (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        report = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Grace",
                    last_name="Hopper",
                    email="grace@example.test",
                    manager_id=manager["id"],
                ),
            )
        ).json()

        terminated = await client.post(
            f"/api/v1/employees/{manager['id']}/termination",
            json={"effective_date": "2026-09-01", "reason": "offboarding"},
        )
        assert terminated.status_code == 200
        body = terminated.json()
        assert body["status"] == "terminated"
        assert body["terminated_at"] == "2026-09-01"

        detail = (await client.get(f"/api/v1/employees/{manager['id']}")).json()
        assert detail["current_employment"] is None  # every period closed
        assert detail["employments"][0]["effective_to"] == "2026-09-01"

        refreshed_report = (
            await client.get(f"/api/v1/employees/{report['id']}")
        ).json()
        assert refreshed_report["manager_id"] is None  # Decision #4


async def test_get_of_terminated_employee_still_returns_200(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        await client.post(
            f"/api/v1/employees/{created['id']}/termination",
            json={"effective_date": "2026-06-01", "reason": "left"},
        )
        response = await client.get(f"/api/v1/employees/{created['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "terminated"
