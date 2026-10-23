"""T6 integration — persisted outbox envelopes match the event contracts.

Drives all 7 catalogued transitions through the HTTP API, then validates every
**persisted** `payload` with `jsonschema.Draft202012Validator` against
`contracts/events/common/event-envelope.v1.json` and its type's `v1` payload
schema, and reruns the PII-key sweep on the stored payloads (AC6). The
port-rejects-a-non-envelope failure case lives in `tests/unit/outbox/`.

This reads persisted rows through the session, which is why it sits in
`tests/integration/` and not `tests/contract/` (the planned deviation from the
feature spec's Surfaces table).
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events"
ENVELOPE_PATH = SCHEMAS_DIR / "common" / "event-envelope.v1.json"
CLOCK = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)  # the late case's edge clock
SHIFT_PAYLOAD = {
    "name": "Always past",
    "start_time": "00:00",
    "end_time": "23:59",
    "grace_minutes": 0,
}

# The sweep's committed order — one row per emitted envelope.
EXPECTED_TYPES = [
    "employee.created",
    "employee.updated",
    "employee.transferred",
    "employee.terminated",
    "attendance.checked_in",
    "attendance.checked_out",
    "attendance.checked_in",
    "attendance.late",
]

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason", "note")

# `event_type` domain → `contracts/events/` directory (the employees domain's
# events live under the plural directory).
_SCHEMA_DIRECTORIES = {
    "employee": "employees",
    "attendance": "attendance",
    "job": "recruitment",
    "candidate": "recruitment",
    "interview": "recruitment",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _schema_for(event_type: str) -> Path:
    """`employee.updated` → `employees/employee-updated.v1.json` (and peers)."""
    domain, name = event_type.split(".", 1)
    directory = _SCHEMA_DIRECTORIES[domain]
    return SCHEMAS_DIR / directory / f"{domain}-{name.replace('_', '-')}.v1.json"


def _pii_keys(obj: Any) -> list[str]:
    """Return keys in the payload tree whose names look PII-shaped."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if any(marker in key.lower() for marker in _PII_KEY_MARKERS):
                found.append(key)
            found.extend(_pii_keys(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_pii_keys(item))
    return found


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


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


async def test_persisted_envelopes_validate_against_their_contracts(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6 — every stored payload conforms to the v1 envelope + payload schema."""
    async with client_factory() as client:
        refs_a = await create_org_refs(client)
        refs_b = await create_org_refs(client, suffix="-B")

        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs_a))
        ).json()
        assert (
            await client.patch(
                f"/api/v1/employees/{created['id']}", json={"first_name": "Augusta"}
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/api/v1/employees/{created['id']}/transfers",
                json={
                    "department_id": refs_b[0],
                    "effective_from": "2026-06-01",
                    "reason": "reorg",
                },
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/api/v1/employees/{created['id']}/termination",
                json={"effective_date": "2026-09-01", "reason": "left"},
            )
        ).status_code == 200

        await _seed_actor(session, refs_a)
        opened = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
        assert (
            await client.post(
                f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
            )
        ).status_code == 200

        # Pin the edge clock past a 00:00 + 0-grace shift: the second check-in
        # of the day is late and emits its second envelope.
        monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
        assert (
            await client.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
        ).status_code == 201
        late = (
            await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
        assert late["late"] is True

    rows = await _outbox_rows(session)
    assert [row.event_type for row in rows] == EXPECTED_TYPES

    envelope_validator = Draft202012Validator(_load(ENVELOPE_PATH))
    for row in rows:
        # The stored envelope as a whole, then the typed payload against its
        # own v1 schema (the mapping mirrors `tests/contract/` helpers).
        envelope_validator.validate(row.payload)
        Draft202012Validator(_load(_schema_for(row.event_type))).validate(
            row.payload["data"]
        )
        # Persisted payloads carry no PII-shaped keys — same sweep as the
        # builder-level contract tests (`.ai/security.md`).
        assert _pii_keys(row.payload) == [], f"PII-shaped key in {row.event_type}"
