"""T5 unit — employee mutations append their envelopes to the outbox.

The service hands each built envelope (the domain's `events.py` builder output)
to the `EventOutbox` port once per transition, before the audit write — the
Postgres adapter flushes it in the request's transaction, so the row, its audit
entry and its event commit together (ADR 0012). No I/O: fakes only.
"""

import json
from datetime import date

import pytest
from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.employees.schemas import (
    TerminationRequest,
    TransferRequest,
    UpdateEmployee,
)
from app.domains.employees.service import EmployeeService
from tests.unit.employees.conftest import (
    ACTOR,
    NOW,
    TENANT_A,
    OrgWorld,
    create_employee,
    identity,
)
from tests.unit.employees.fakes import FakeEmployeeRepo, FakeEmploymentRepo
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

LATER = date(2026, 1, 1)  # inside [hired_at, today] — a valid effective date


async def test_create_appends_the_created_envelope(
    service: EmployeeService, orgs: OrgWorld, outbox: RecordingEventOutbox
) -> None:
    refs = await orgs.seed()

    created = await create_employee(service, refs)

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "employee.created"
    assert envelope["version"] == 1
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["actor_id"] == str(ACTOR)
    assert envelope["occurred_at"] == NOW.isoformat()  # the injected domain clock
    assert envelope["data"] == {
        "employee_id": str(created.id),
        "department_id": str(refs[0]),
        "position_id": str(refs[1]),
        "location_id": str(refs[2]),
        "employment_type": "full_time",
    }


async def test_update_appends_field_names_only(
    service: EmployeeService, orgs: OrgWorld, outbox: RecordingEventOutbox
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    outbox.envelopes.clear()

    await service.update(
        identity(),
        created.id,
        UpdateEmployee(first_name="Augusta", phone="+84-000"),
        NOW,
    )

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "employee.updated"
    assert envelope["data"] == {
        "employee_id": str(created.id),
        "changed_fields": ["first_name", "phone"],
    }
    # Field names only, never the values (PII rule).
    assert "Augusta" not in json.dumps(envelope)
    assert "+84-000" not in json.dumps(envelope)


async def test_empty_update_appends_nothing(
    service: EmployeeService, orgs: OrgWorld, outbox: RecordingEventOutbox
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    outbox.envelopes.clear()

    await service.update(identity(), created.id, UpdateEmployee(), NOW)

    assert outbox.envelopes == []  # no transition → no event


async def test_transfer_appends_from_and_to_departments(
    service: EmployeeService, orgs: OrgWorld, outbox: RecordingEventOutbox
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    outbox.envelopes.clear()
    target_department = uuid7()
    await orgs.department_repo.add(TENANT_A, target_department, "Ops", None)

    period = await service.transfer(
        identity(),
        created.id,
        TransferRequest(department_id=target_department, effective_from=LATER),
        NOW,
    )

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "employee.transferred"
    assert envelope["data"] == {
        "employee_id": str(created.id),
        "from_department_id": str(refs[0]),  # the closed assignment
        "to_department_id": str(target_department),
        "effective_from": LATER.isoformat(),
    }
    assert period.department_id == target_department


async def test_terminate_appends_one_envelope_managers_included(
    service: EmployeeService, orgs: OrgWorld, outbox: RecordingEventOutbox
) -> None:
    refs = await orgs.seed()
    manager = await create_employee(service, refs)
    await create_employee(
        service, refs, email="report@example.test", manager_id=manager.id
    )
    outbox.envelopes.clear()

    await service.terminate(
        identity(),
        manager.id,
        TerminationRequest(effective_date=LATER, reason="resigned"),
        NOW,
    )

    [envelope] = outbox.envelopes  # manager_cleared audit entries emit nothing
    assert envelope["event_type"] == "employee.terminated"
    assert envelope["data"] == {
        "employee_id": str(manager.id),
        "effective_date": LATER.isoformat(),
    }
    # The reason stays on the audit entry, never in the event.
    assert "resigned" not in json.dumps(envelope)


async def test_rejected_mutation_appends_nothing(
    service: EmployeeService, outbox: RecordingEventOutbox
) -> None:
    """A mutation that fails before its append leaves the outbox untouched."""
    with pytest.raises(EmployeeNotFoundError):
        await service.update(
            identity(), uuid7(), UpdateEmployee(first_name="Ghost"), NOW
        )

    assert outbox.envelopes == []


async def test_outbox_failure_propagates_before_the_audit_write(
    repo: FakeEmployeeRepo,
    employments: FakeEmploymentRepo,
    orgs: OrgWorld,
    audit: RecordingAuditWriter,
) -> None:
    """An append failure is never swallowed: the mutation fails loudly.

    The service must not catch outbox errors (the real adapter's failure has to
    roll the request transaction back), and the append must precede the audit
    write — no audit entry may exist for a mutation whose event was never
    stored.
    """
    refs = await orgs.seed()
    failing = RecordingEventOutbox(error=RuntimeError("outbox unavailable"))
    service = EmployeeService(
        repo=repo,
        employments=employments,
        departments=orgs.departments,
        positions=orgs.positions,
        locations=orgs.locations,
        audit=audit,
        outbox=failing,
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await create_employee(service, refs)

    assert [env["event_type"] for env in failing.envelopes] == ["employee.created"]
    assert audit.entries == []
