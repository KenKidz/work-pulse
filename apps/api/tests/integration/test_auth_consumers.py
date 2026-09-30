"""AC10 — the session → `employee_id` consumer regression (the T5 swap).

A user linked to employee E must get E from `/employees/me`, and check-in must
open E's record — the identity carries the employee link, never the acting user
id. A user without a link fails closed (`404 employee_not_found`) on both
employee-scoped flows, never a server error (pinned).
"""

from tests.integration.conftest import (
    ClientFactory,
    create_org_refs,
    employee_payload,
)

PASSWORD = "correct-horse-battery"


async def test_linked_user_resolves_employee_me_and_check_in(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        employee = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        linked = await admin.post(
            "/api/v1/users",
            json={
                "email": "ada@workpulse.test",
                "password": PASSWORD,
                "role": "employee",
                "employee_id": employee["id"],
            },
        )
        assert linked.status_code == 201

    async with client_factory(None) as worker:
        login = await worker.post(
            "/api/v1/auth/login",
            json={"email": "ada@workpulse.test", "password": PASSWORD},
        )
        assert login.status_code == 200

        me = await worker.get("/api/v1/employees/me")
        assert me.status_code == 200
        assert me.json()["id"] == employee["id"]
        assert me.json()["employee_code"] == employee["employee_code"]

        check_in = await worker.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert check_in.status_code == 201
        assert check_in.json()["employee_id"] == employee["id"]

        # Self-scope sees exactly E's open record.
        records = (await worker.get("/api/v1/attendance/records")).json()
        assert [item["employee_id"] for item in records["items"]] == [employee["id"]]


async def test_unlinked_user_fails_closed_on_employee_scoped_flows(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        created = await admin.post(
            "/api/v1/users",
            json={
                "email": "finance@workpulse.test",
                "password": PASSWORD,
                "role": "finance",
            },
        )
        assert created.status_code == 201
        assert created.json()["employee_id"] is None

    async with client_factory(None) as unlinked:
        login = await unlinked.post(
            "/api/v1/auth/login",
            json={"email": "finance@workpulse.test", "password": PASSWORD},
        )
        assert login.status_code == 200

        me = await unlinked.get("/api/v1/employees/me")
        assert me.status_code == 404
        assert me.json()["error"]["code"] == "employee_not_found"

        check_in = await unlinked.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert check_in.status_code == 404
        assert check_in.json()["error"]["code"] == "employee_not_found"
