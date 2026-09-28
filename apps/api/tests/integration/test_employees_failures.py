"""Failure-path HTTP tests for /api/v1/employees — AC4, AC5, AC6, pinned codes."""

from uuid import UUID

from httpx import AsyncClient, Response

from tests.integration.conftest import (
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
)

UNKNOWN_ID = "00000000-0000-4000-8000-00000000dead"


async def _create(
    client: AsyncClient,
    refs: tuple[str, str, str],
    *,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.test",
    manager_id: str | None = None,
    employee_code: str | None = None,
) -> Response:
    return await client.post(
        "/api/v1/employees",
        json=employee_payload(
            refs,
            first_name=first_name,
            last_name=last_name,
            email=email,
            manager_id=manager_id,
            employee_code=employee_code,
        ),
    )


async def test_duplicate_email_returns_409(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        assert (await _create(client, refs)).status_code == 201
        duplicate = await _create(client, refs, employee_code="EMP-00002")

    assert duplicate.status_code == 409  # AC4
    assert duplicate.json() == {
        "error": {
            "code": "employee_email_taken",
            "message": "Employee email already taken in this tenant",
        }
    }
    assert "ada@example.test" not in duplicate.text  # never echo field values


async def test_case_variant_email_returns_409(client_factory: ClientFactory) -> None:
    """Email uniqueness is case-insensitive (functional unique index)."""
    async with client_factory() as client:
        refs = await create_org_refs(client)
        assert (await _create(client, refs)).status_code == 201
        case_variant = await _create(
            client, refs, email="ADA@EXAMPLE.TEST", employee_code="EMP-00002"
        )

    assert case_variant.status_code == 409
    assert case_variant.json()["error"]["code"] == "employee_email_taken"


async def test_duplicate_code_returns_409(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        assert (
            await _create(client, refs, employee_code="EMP-00042")
        ).status_code == 201
        duplicate = await _create(
            client, refs, email="other@example.test", employee_code="EMP-00042"
        )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "employee_code_taken"


async def test_self_manager_returns_400(client_factory: ClientFactory) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (await _create(client, refs)).json()
        response = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={
                "department_id": refs[0],
                "manager_id": created["id"],
                "effective_from": "2026-06-01",
            },
        )

    assert response.status_code == 400  # AC5
    assert response.json()["error"]["code"] == "employee_self_manager"


async def test_manager_invalid_returns_400(client_factory: ClientFactory) -> None:
    unknown_manager = UUID("00000000-0000-4000-8000-00000000beef")
    async with client_factory() as client:
        refs = await create_org_refs(client)
        missing = await _create(client, refs, manager_id=str(unknown_manager))

        manager = (await _create(client, refs)).json()
        await client.post(
            f"/api/v1/employees/{manager['id']}/termination",
            json={"effective_date": "2026-06-01", "reason": "left"},
        )
        terminated_manager = await _create(
            client, refs, email="other@example.test", manager_id=manager["id"]
        )

    for response in (missing, terminated_manager):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "employee_manager_invalid"


async def test_transfer_to_archived_department_returns_409(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        archived_refs = await create_org_refs(client, suffix="-Legacy")
        archived = await client.post(f"/api/v1/departments/{archived_refs[0]}/archive")
        assert archived.status_code == 200

        created = (await _create(client, refs)).json()
        response = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={
                "department_id": archived_refs[0],
                "effective_from": "2026-06-01",
            },
        )

    assert response.status_code == 409  # AC6
    assert response.json()["error"]["code"] == "transfer_target_archived"


async def test_create_with_archived_reference_reuses_the_code(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        await client.post(f"/api/v1/departments/{refs[0]}/archive")
        # An empty department archives cleanly; employees must not attach to it.
        response = await _create(client, refs)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transfer_target_archived"


async def test_transfer_effective_date_failures(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (await _create(client, refs)).json()

        before_hire = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={"department_id": refs[0], "effective_from": "2020-01-01"},
        )
        in_the_future = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={"department_id": refs[0], "effective_from": "2099-01-01"},
        )

    for response in (before_hire, in_the_future):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "employee_transfer_effective_invalid"


async def test_backdating_past_current_period_returns_409_overlap(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (await _create(client, refs)).json()
        first = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={"department_id": refs[0], "effective_from": "2026-06-01"},
        )
        assert first.status_code == 200

        response = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={"department_id": refs[0], "effective_from": "2026-03-01"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "employment_period_overlap"


async def test_terminated_employee_cannot_terminate_or_transfer_again(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (await _create(client, refs)).json()
        await client.post(
            f"/api/v1/employees/{created['id']}/termination",
            json={"effective_date": "2026-06-01", "reason": "left"},
        )

        second_termination = await client.post(
            f"/api/v1/employees/{created['id']}/termination",
            json={"effective_date": "2026-06-01", "reason": "again"},
        )
        transfer = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={"department_id": refs[0], "effective_from": "2026-06-01"},
        )

    for response in (second_termination, transfer):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "employee_already_terminated"


async def test_unknown_employee_is_404_everywhere(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        responses = [
            await client.get(f"/api/v1/employees/{UNKNOWN_ID}"),
            await client.patch(
                f"/api/v1/employees/{UNKNOWN_ID}", json={"first_name": "X"}
            ),
            await client.post(
                f"/api/v1/employees/{UNKNOWN_ID}/transfers",
                json={"department_id": refs[0], "effective_from": "2026-06-01"},
            ),
            await client.post(
                f"/api/v1/employees/{UNKNOWN_ID}/termination",
                json={"effective_date": "2026-06-01", "reason": "x"},
            ),
            await client.get(f"/api/v1/employees/{UNKNOWN_ID}/employments"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "employee_not_found"


async def test_unknown_department_reference_is_404(
    client_factory: ClientFactory,
) -> None:
    unknown = str(UUID("00000000-0000-4000-8000-00000000dead"))
    async with client_factory() as client:
        refs = await create_org_refs(client)
        response = await _create(
            client,
            (unknown, refs[1], refs[2]),
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "department_not_found"


async def test_validation_failures_return_422_without_echoing_values(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (await _create(client, refs)).json()

        null_name = await client.patch(
            f"/api/v1/employees/{created['id']}", json={"first_name": None}
        )
        null_position = await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={
                "department_id": refs[0],
                "position_id": None,
                "effective_from": "2026-06-01",
            },
        )
        missing_reason = await client.post(
            f"/api/v1/employees/{created['id']}/termination",
            json={"effective_date": "2026-06-01"},
        )
        bad_status = await client.get(
            "/api/v1/employees", params={"status": "not-a-status"}
        )

    for response in (null_name, null_position, missing_reason, bad_status):
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert "Ada" not in body["error"]["message"]  # never echo field values
        assert str(TENANT_A) not in body["error"]["message"]
