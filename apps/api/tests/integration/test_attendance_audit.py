"""AC11 — one audit entry per check-in/check-out; note values never in `changes`."""

import json
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    USER,
    ClientFactory,
    create_org_refs,
)

PII_CHECK_IN_NOTE = "pii-check-in-note"
PII_CHECK_OUT_NOTE = "pii-check-out-note"


def _changes(row: AuditEntry) -> dict[str, Any]:
    assert row.changes is not None
    return row.changes


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


async def _rows(session: AsyncSession) -> list[AuditEntry]:
    """Attendance/shift entries only — org setup via HTTP is audited too."""
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type.in_(("attendance", "shift")))
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def test_every_mutation_writes_one_entry_without_note_values(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        record = (
            await client.post(
                "/api/v1/attendance/check-ins",
                json={"work_mode": "onsite", "check_in_note": PII_CHECK_IN_NOTE},
            )
        ).json()
        closed = await client.post(
            f"/api/v1/attendance/records/{record['id']}/check-out",
            json={"check_out_note": PII_CHECK_OUT_NOTE},
        )
        assert closed.status_code == 200
        shift = (
            await client.post(
                "/api/v1/shifts",
                json={
                    "name": "Day",
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "grace_minutes": 10,
                },
            )
        ).json()
        patched = await client.patch(
            f"/api/v1/shifts/{shift['id']}", json={"grace_minutes": 15}
        )
        assert patched.status_code == 200

    rows = await _rows(session)
    assert [row.action for row in rows] == [
        "attendance.checked_in",
        "attendance.checked_out",
        "shift.created",
        "shift.updated",
    ]

    checked_in = rows[0]
    assert checked_in.tenant_id == TENANT_A
    assert checked_in.actor_id == USER  # the acting user id
    assert str(checked_in.entity_id) == record["id"]
    assert checked_in.occurred_at is not None
    assert _changes(checked_in)["employee_id"]["new"] == str(ACTOR)
    assert _changes(checked_in)["workday"]["new"] == record["workday"]
    assert _changes(checked_in)["work_mode"]["new"] == "onsite"
    assert _changes(checked_in)["late"]["new"] is False

    checked_out = rows[1]
    assert str(checked_out.entity_id) == record["id"]
    assert (
        _changes(checked_out)["duration_minutes"]["new"]
        == closed.json()["duration_minutes"]
    )

    shift_created = rows[2]
    assert str(shift_created.entity_id) == shift["id"]
    assert _changes(shift_created)["name"]["new"] == "Day"
    assert _changes(rows[3])["grace_minutes"] == {"old": 10, "new": 15}

    # Notes are PII: never in `changes`, never echoed anywhere but the record.
    dumped = json.dumps([row.changes for row in rows])
    assert PII_CHECK_IN_NOTE not in dumped
    assert PII_CHECK_OUT_NOTE not in dumped


async def test_failed_mutations_write_no_rows(
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
        duplicate = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert duplicate.status_code == 409
        missing = await client.post(
            f"/api/v1/attendance/records/{uuid4()}/check-out", json={}
        )
        assert missing.status_code == 404

    rows = await _rows(session)
    assert [row.action for row in rows] == ["attendance.checked_in"]
