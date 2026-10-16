"""T4 integration — the attendance capture path writes through the outbox.

Against real PostgreSQL (`postgres-test`):

- **AC1**: a check-in — and a late check-in's second envelope — lands in
  `event_outbox` in the same transaction as the attendance record and its
  audit entry, stamped with the actor's `tenant_id`, envelopes verbatim.
- **AC3 (service half)**: a mutation whose audit write fails *after* the append
  rolls the whole transaction back — neither the business row nor the outbox
  row survives. Driven through the real `get_session` lifecycle (`athrow`, the
  FastAPI teardown path: the `client_factory` override intentionally does not
  roll back, so the rollback proof must not ride on it — risk R2).
- **AC4**: denied (403), unlinked (404) and duplicate (409) captures write no
  outbox row.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.api.v1 import attendance as attendance_api
from app.dependencies import get_attendance_service
from app.domains.attendance import models as attendance_orm
from app.domains.attendance.schemas import CheckInRequest
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.infrastructure.postgres.session import get_session
from app.shared.audit import AuditEntryCreate
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)  # the patched edge clock
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # the rollback test's now
SHIFT_PAYLOAD = {
    "name": "Always past",
    "start_time": "00:00",
    "end_time": "23:59",
    "grace_minutes": 0,
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


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


async def _count(session: AsyncSession, model: Any) -> int:
    stmt = select(func.count()).select_from(model)
    return (await session.execute(stmt)).scalar_one()


async def test_check_in_appends_the_checked_in_envelope(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
    assert response.status_code == 201
    record = response.json()

    rows = await _outbox_rows(session)
    assert [row.event_type for row in rows] == ["attendance.checked_in"]
    row = rows[0]
    assert row.tenant_id == TENANT_A
    assert row.published_at is None
    assert row.attempts == 0
    assert row.last_error is None
    assert row.event_id.startswith("evt_")

    payload = row.payload
    assert payload["event_id"] == row.event_id
    assert payload["version"] == 1
    assert payload["tenant_id"] == str(TENANT_A)
    assert payload["actor_id"] == str(USER)
    # Never re-stamped: the envelope carries the capture instant verbatim.
    assert datetime.fromisoformat(payload["occurred_at"]) == datetime.fromisoformat(
        record["checked_in_at"]
    )
    data = payload["data"]
    assert data["attendance_id"] == record["id"]
    assert data["employee_id"] == str(ACTOR)
    assert data["workday"] == record["workday"]
    assert data["work_mode"] == "onsite"
    assert data["late"] is False

    # The same transaction holds the record and its audit entry: one session
    # sees all three (the atomicity proof is the rollback test below).
    db_record = (
        await session.execute(
            select(attendance_orm.AttendanceRecord).where(
                attendance_orm.AttendanceRecord.id == UUID(record["id"])
            )
        )
    ).scalar_one()
    assert db_record.checked_in_at == datetime.fromisoformat(record["checked_in_at"])
    audit = (
        (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.entity_id == UUID(record["id"]),
                    AuditEntry.action == "attendance.checked_in",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1


async def test_late_check_in_appends_checked_in_then_late(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        shift = (await admin.post("/api/v1/shifts", json=SHIFT_PAYLOAD)).json()
    await _seed_actor(session, refs)

    async with client_factory() as client:
        record = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    assert record["late"] is True
    assert record["workday"] == CLOCK.date().isoformat()

    rows = await _outbox_rows(session)
    # Fetched in id order (the relay's poll order): same-transaction builder
    # order preserved means `checked_in` comes before `late` (AC8 rides on
    # this), and UUIDv7 keeps that order in the ids themselves.
    assert [row.event_type for row in rows] == [
        "attendance.checked_in",
        "attendance.late",
    ]

    checked_in, late = rows
    for row in rows:
        assert row.tenant_id == TENANT_A
        assert row.published_at is None
        assert row.attempts == 0

    assert checked_in.payload["occurred_at"] == CLOCK.astimezone(UTC).isoformat()
    assert checked_in.payload["data"]["late"] is True

    late_data = late.payload["data"]
    assert late.payload["event_type"] == "attendance.late"
    assert late_data["attendance_id"] == record["id"]
    assert late_data["employee_id"] == str(ACTOR)
    assert late_data["workday"] == record["workday"]
    assert late_data["shift_id"] == shift["id"]
    assert late_data["minutes_late"] == 660  # 11:00 past a 00:00 + 0-grace shift


async def test_failed_mutation_rolls_back_record_and_outbox_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 (service half) through the production session lifecycle.

    The audit writer raises after the record, the outbox row and the presence
    fact were produced; the `get_session` teardown (`athrow` — what FastAPI
    does when the handler raises) rolls all of it back.
    """
    lifecycle = cast(AsyncGenerator[AsyncSession, None], get_session())
    db_session = await anext(lifecycle)

    # Seed the actor directly on the lifecycle session, then commit: the
    # rollback below must only discard the failed mutation's writes.
    department = await PostgresDepartmentRepository(db_session).add(
        TENANT_A, uuid7(), "Engineering", None
    )
    position = await PostgresPositionRepository(db_session).add(
        TENANT_A, uuid7(), "Engineer", None
    )
    location = await PostgresLocationRepository(db_session).add(
        TENANT_A, uuid7(), "HQ", None, "UTC"
    )
    await _seed_actor(
        db_session, (str(department.id), str(position.id), str(location.id))
    )
    await db_session.commit()

    async def _audit_down(self: PostgresAuditWriter, entry: AuditEntryCreate) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(PostgresAuditWriter, "write", _audit_down)
    service = get_attendance_service(db_session)

    try:
        await service.check_in(identity(), CheckInRequest(work_mode="onsite"), NOW)
    except RuntimeError as exc:
        # Both writes already happened (flushed) — the failure came after the
        # append, so the rollback assertions below are not vacuous.
        assert await _count(db_session, attendance_orm.AttendanceRecord) == 1
        assert await _count(db_session, EventOutboxRow) == 1
        # FastAPI tears the dependency generator down with the handler's own
        # exception; `get_session` rolls back and re-raises it.
        with pytest.raises(RuntimeError, match="audit store unavailable"):
            await lifecycle.athrow(exc)
    else:  # pragma: no cover — the monkeypatched writer always raises
        raise AssertionError("check_in should have failed after its append")

    async with get_session_factory()() as fresh:
        assert await _count(fresh, attendance_orm.AttendanceRecord) == 0
        assert await _count(fresh, EventOutboxRow) == 0


async def test_denied_and_rejected_mutations_write_no_outbox_row(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)

    async with client_factory() as admin:
        first = await admin.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert first.status_code == 201

    # 403: shift management is hr/admin/super_admin only — the attendance
    # module's denied-role mutation; nothing may reach the outbox.
    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        denied = await employee.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"

    # 404: a user without an employee link cannot check in (self-only capture).
    async with client_factory(identity(employee_id=None)) as unlinked:
        orphan = await unlinked.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert orphan.status_code == 404

    # 409: the actor's open record already exists — the duplicate is rejected.
    async with client_factory() as admin:
        duplicate = await admin.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert duplicate.status_code == 409

    # Exactly the one committed mutation wrote a row — none of the rejections.
    rows = await _outbox_rows(session)
    assert [row.event_type for row in rows] == ["attendance.checked_in"]
    assert rows[0].payload["data"]["attendance_id"] == first.json()["id"]
