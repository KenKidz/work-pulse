"""AC10 — one audit entry per mutation, `changes` carry no PII values."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models_audit import AuditEntry
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity_headers,
)

PII_VALUES = ["Zorblax", "Qqweird", "pii-unique@example.test", "+84-555-0100"]


async def _rows(session: AsyncSession) -> list[AuditEntry]:
    """Only employee entries — org setup via HTTP is audited too."""
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "employee")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def test_full_lifecycle_writes_one_row_per_mutation(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs_a = await create_org_refs(client)
        refs_b = await create_org_refs(client, suffix="-B")
        manager = (
            await client.post("/api/v1/employees", json=employee_payload(refs_a))
        ).json()
        report = (
            await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs_a,
                    first_name="Zorblax",
                    last_name="Qqweird",
                    email="pii-unique@example.test",
                    phone="+84-555-0100",
                    manager_id=manager["id"],
                ),
            )
        ).json()
        await client.patch(
            f"/api/v1/employees/{report['id']}",
            json={"first_name": "Renamed"},
        )
        await client.post(
            f"/api/v1/employees/{report['id']}/transfers",
            json={"department_id": refs_b[0], "effective_from": "2026-06-01"},
        )
        await client.post(
            f"/api/v1/employees/{manager['id']}/termination",
            json={"effective_date": "2026-09-01", "reason": "offboarding"},
        )

    rows = await _rows(session)
    assert [row.action for row in rows] == [
        "employee.created",
        "employee.created",
        "employee.updated",
        "employee.transferred",
        "employee.terminated",
        "employee.manager_cleared",
    ]
    for row in rows:
        assert row.tenant_id == TENANT_A
        assert row.actor_id == ACTOR
        assert row.occurred_at is not None

    assert str(rows[0].entity_id) == manager["id"]
    assert str(rows[2].entity_id) == report["id"]
    cleared = rows[5]
    assert str(cleared.entity_id) == report["id"]
    assert cleared.changes == {"manager_id": {"old": manager["id"], "new": None}}
    assert rows[4].reason == "offboarding"  # reason lives on the audit entry only

    # AC10: no name/email/phone value anywhere in `changes`.
    dumped = json.dumps([row.changes for row in rows])
    for value in PII_VALUES:
        assert value not in dumped
    assert "Renamed" not in dumped
    assert "offboarding" not in dumped


async def test_failed_mutations_write_no_rows(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        assert (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).status_code == 201
        duplicate = await client.post(
            "/api/v1/employees", json=employee_payload(refs, employee_code="EMP-00002")
        )
        assert duplicate.status_code == 409

    async with client_factory(identity_headers(role="manager")) as denied:
        rejected = await denied.post(
            "/api/v1/employees", json=employee_payload(refs, employee_code="EMP-00003")
        )
        assert rejected.status_code == 403

    rows = await _rows(session)
    assert [row.action for row in rows] == ["employee.created"]


async def test_update_changes_record_field_names_only(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
        await client.patch(
            f"/api/v1/employees/{created['id']}",
            json={"first_name": "Augusta", "email": "augusta@example.test"},
        )

    rows = await _rows(session)
    updated = rows[1]
    assert updated.changes == {"first_name": "changed", "email": "changed"}
    assert "Augusta" not in json.dumps(updated.changes)
    assert "augusta@example.test" not in json.dumps(updated.changes)
