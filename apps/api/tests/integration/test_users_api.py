"""HTTP integration tests for `/api/v1/users` — AC7 happy paths + pinned contract.

The acting identity is injected through the `get_identity` dependency override
(`client_factory()` = the tenant-A admin). Password material is only ever
asserted absent from responses and audit payloads; the pinned `User` key set is
asserted exactly.
"""

from httpx import AsyncClient, Response

from tests.integration.conftest import (
    PINNED_USER_KEYS,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
)

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "stapled-battery-wrong"


async def _create_user(
    client: AsyncClient,
    *,
    email: str = "ada@workpulse.test",
    password: str = PASSWORD,
    role: str = "employee",
    employee_id: str | None = None,
) -> Response:
    payload: dict[str, object] = {"email": email, "password": password, "role": role}
    if employee_id is not None:
        payload["employee_id"] = employee_id
    return await client.post("/api/v1/users", json=payload)


async def test_create_with_employee_link_returns_201_exact_shape(
    client_factory: ClientFactory,
) -> None:
    """AC7 (happy half): create with a link → `201`, pinned `User`, no secrets."""
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        employee = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()

        response = await _create_user(admin, employee_id=employee["id"])
        assert response.status_code == 201
        body = response.json()
        assert set(body) == PINNED_USER_KEYS  # exact pinned shape
        assert body["tenant_id"] == str(TENANT_A)
        assert body["email"] == "ada@workpulse.test"
        assert body["role"] == "employee"
        assert body["employee_id"] == employee["id"]
        assert body["active"] is True
        assert body["created_at"] is not None and body["updated_at"] is not None

        # Never password or session material — not even hashes.
        assert PASSWORD not in response.text
        assert "argon2" not in response.text

        fetched = await admin.get(f"/api/v1/users/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == body


async def test_create_without_employee_link_supports_non_employee_roles(
    client_factory: ClientFactory,
) -> None:
    """Decision 2: finance/super_admin accounts need no employee link."""
    async with client_factory() as admin:
        finance = await _create_user(
            admin, email="finance@workpulse.test", role="finance"
        )
        super_admin = await _create_user(
            admin, email="root@workpulse.test", role="super_admin"
        )

    assert finance.status_code == 201
    assert finance.json()["employee_id"] is None
    assert super_admin.status_code == 201
    assert super_admin.json()["role"] == "super_admin"


async def test_list_filters_pagination_and_stable_order(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        ada = (await _create_user(admin, email="ada@workpulse.test")).json()
        bo = (await _create_user(admin, email="bo@workpulse.test", role="hr")).json()
        cy = (await _create_user(admin, email="cy@workpulse.test")).json()
        deactivated = await admin.post(f"/api/v1/users/{cy['id']}/deactivate")
        assert deactivated.status_code == 200

        # Pinned default order: email ascending.
        default = (await admin.get("/api/v1/users")).json()
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
        assert all(set(item) == PINNED_USER_KEYS for item in default["items"])

        # Filters compose: `q` is a case-insensitive email substring.
        by_q = (await admin.get("/api/v1/users", params={"q": "BO"})).json()
        assert [item["id"] for item in by_q["items"]] == [bo["id"]]
        by_role = (await admin.get("/api/v1/users", params={"role": "hr"})).json()
        assert [item["id"] for item in by_role["items"]] == [bo["id"]]
        active_only = (await admin.get("/api/v1/users", params={"active": True})).json()
        assert [item["id"] for item in active_only["items"]] == [ada["id"], bo["id"]]
        inactive = (await admin.get("/api/v1/users", params={"active": False})).json()
        assert [item["id"] for item in inactive["items"]] == [cy["id"]]

        # Pagination stays stable across pages.
        page1 = (
            await admin.get("/api/v1/users", params={"page": 1, "page_size": 2})
        ).json()
        page2 = (
            await admin.get("/api/v1/users", params={"page": 2, "page_size": 2})
        ).json()
        assert [item["id"] for item in page1["items"]] == [ada["id"], bo["id"]]
        assert [item["id"] for item in page2["items"]] == [cy["id"]]
        assert page2["page"]["total_pages"] == 2

        # Out-of-bound values clamp, never fail (pinned Phase 1 shape).
        clamped = (await admin.get("/api/v1/users", params={"page_size": 1000})).json()
        assert clamped["page"]["page_size"] == 100
        page_zero = (await admin.get("/api/v1/users", params={"page": 0})).json()
        assert page_zero["page"]["page"] == 1

    assert deactivated.json()["active"] is False


async def test_patch_semantics_absent_unchanged_and_null_unlinks(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        employee = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        created = (await _create_user(admin, employee_id=employee["id"])).json()

        # Email change: everything else untouched.
        emailed = await admin.patch(
            f"/api/v1/users/{created['id']}", json={"email": "augusta@workpulse.test"}
        )
        assert emailed.status_code == 200
        assert emailed.json()["email"] == "augusta@workpulse.test"
        assert emailed.json()["role"] == "employee"
        assert emailed.json()["employee_id"] == employee["id"]

        # Role change re-checks nothing but records `role` (audit separate file).
        promoted = await admin.patch(
            f"/api/v1/users/{created['id']}", json={"role": "manager"}
        )
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "manager"

        # Explicit `"employee_id": null` unlinks (pinned PATCH semantics).
        unlinked = await admin.patch(
            f"/api/v1/users/{created['id']}", json={"employee_id": None}
        )
        assert unlinked.status_code == 200
        assert unlinked.json()["employee_id"] is None
        assert unlinked.json()["role"] == "manager"

        # Empty patch is a no-op.
        noop = await admin.patch(f"/api/v1/users/{created['id']}", json={})
        assert noop.status_code == 200
        assert noop.json() == unlinked.json()

        # `active` is never in PATCH — the field is ignored (explicit routes only).
        ignored = await admin.patch(
            f"/api/v1/users/{created['id']}", json={"active": False}
        )
        assert ignored.status_code == 200
        assert ignored.json()["active"] is True


async def test_linking_a_terminated_employee_is_allowed(
    client_factory: ClientFactory,
) -> None:
    """Pinned edge case: accounts and employment are separate lifecycles.

    Exercises the real `PostgresEmployeeLink.exists` over HTTP (not the unit
    fake): the link is judged on employee existence alone, never on status.
    """
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        first = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        second = (
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
        for employee in (first, second):
            terminated = await admin.post(
                f"/api/v1/employees/{employee['id']}/termination",
                json={"effective_date": "2026-09-01", "reason": "offboarding"},
            )
            assert terminated.status_code == 200
            assert terminated.json()["status"] == "terminated"

        # Create-with-link path.
        linked = await _create_user(admin, employee_id=first["id"])
        assert linked.status_code == 201
        assert linked.json()["employee_id"] == first["id"]

        # PATCH-link path.
        unlinked = (await _create_user(admin, email="patched@workpulse.test")).json()
        patched = await admin.patch(
            f"/api/v1/users/{unlinked['id']}", json={"employee_id": second["id"]}
        )
        assert patched.status_code == 200
        assert patched.json()["employee_id"] == second["id"]


async def test_activate_deactivate_round_trip_is_idempotent(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        created = (await _create_user(admin)).json()

        deactivated = await admin.post(f"/api/v1/users/{created['id']}/deactivate")
        assert deactivated.status_code == 200
        assert deactivated.json()["active"] is False
        again = await admin.post(f"/api/v1/users/{created['id']}/deactivate")
        assert again.status_code == 200  # idempotent no-op
        assert again.json()["active"] is False

        activated = await admin.post(f"/api/v1/users/{created['id']}/activate")
        assert activated.status_code == 200
        assert activated.json()["active"] is True
        again = await admin.post(f"/api/v1/users/{created['id']}/activate")
        assert again.status_code == 200
        assert again.json()["active"] is True


async def test_reset_password_revokes_sessions_and_new_password_logs_in(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        created = (await _create_user(admin)).json()

    async with client_factory(None) as user_client:
        login = await user_client.post(
            "/api/v1/auth/login",
            json={"email": "ada@workpulse.test", "password": PASSWORD},
        )
        assert login.status_code == 200
        assert (await user_client.get("/api/v1/auth/me")).status_code == 200

        async with client_factory() as admin:
            reset = await admin.post(
                f"/api/v1/users/{created['id']}/password",
                json={"new_password": NEW_PASSWORD},
            )
            assert reset.status_code == 204

        # Every session of that user is revoked by the reset.
        assert (await user_client.get("/api/v1/auth/me")).status_code == 401

    async with client_factory(None) as fresh:
        old = await fresh.post(
            "/api/v1/auth/login",
            json={"email": "ada@workpulse.test", "password": PASSWORD},
        )
        assert old.status_code == 401
        new = await fresh.post(
            "/api/v1/auth/login",
            json={"email": "ada@workpulse.test", "password": NEW_PASSWORD},
        )
        assert new.status_code == 200
