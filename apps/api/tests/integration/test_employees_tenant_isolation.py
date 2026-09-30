"""AC7 — tenant isolation: B touching A's employee is always `404`, never `403`."""

from tests.integration.conftest import (
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)


async def test_tenant_b_cannot_touch_tenant_a_employee(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as tenant_a:
        refs = await create_org_refs(tenant_a)
        created = (
            await tenant_a.post("/api/v1/employees", json=employee_payload(refs))
        ).json()

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        own_refs = await create_org_refs(tenant_b)
        responses = [
            await tenant_b.get(f"/api/v1/employees/{created['id']}"),
            await tenant_b.get(f"/api/v1/employees/{created['id']}/employments"),
            await tenant_b.patch(
                f"/api/v1/employees/{created['id']}", json={"first_name": "Sneaky"}
            ),
            await tenant_b.post(
                f"/api/v1/employees/{created['id']}/transfers",
                json={
                    "department_id": own_refs[0],
                    "effective_from": "2026-06-01",
                },
            ),
            await tenant_b.post(
                f"/api/v1/employees/{created['id']}/termination",
                json={"effective_date": "2026-06-01", "reason": "sneaky"},
            ),
        ]
        listed = await tenant_b.get("/api/v1/employees")

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "employee_not_found"
    assert listed.json()["page"]["total_items"] == 0  # B sees only its own tenant


async def test_tenant_b_cannot_reference_tenant_a_rows(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as tenant_a:
        foreign_refs = await create_org_refs(tenant_a)
        foreign_manager = (
            await tenant_a.post(
                "/api/v1/employees", json=employee_payload(foreign_refs)
            )
        ).json()

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        own_refs = await create_org_refs(tenant_b)
        foreign_department = await tenant_b.post(
            "/api/v1/employees",
            json=employee_payload(own_refs, department_id=foreign_refs[0]),
        )
        foreign_manager_response = await tenant_b.post(
            "/api/v1/employees",
            json=employee_payload(own_refs, manager_id=foreign_manager["id"]),
        )

    # Cross-tenant references surface as not-found / invalid — never 403.
    assert foreign_department.status_code == 404
    assert foreign_department.json()["error"]["code"] == "department_not_found"
    assert foreign_manager_response.status_code == 400
    assert (
        foreign_manager_response.json()["error"]["code"] == "employee_manager_invalid"
    )


async def test_employee_email_uniqueness_is_per_tenant(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as tenant_a:
        refs_a = await create_org_refs(tenant_a)
        first = await tenant_a.post("/api/v1/employees", json=employee_payload(refs_a))
    assert first.status_code == 201

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        refs_b = await create_org_refs(tenant_b)
        second = await tenant_b.post("/api/v1/employees", json=employee_payload(refs_b))

    assert second.status_code == 201  # same email, different tenant
    assert second.json()["employee_code"] == "EMP-00001"
