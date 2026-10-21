"""Payroll event builders pass the outbox gate and carry no amounts (T1/T3)."""

from datetime import UTC, datetime
from uuid import UUID

from app.domains.payroll.events import payroll_calculated, payslip_issued
from app.shared.outbox import EventEnvelope, kafka_message_key

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
RUN = UUID("00000000-0000-4000-8000-000000000005")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")
PAYSLIP = UUID("00000000-0000-4000-8000-000000000006")
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

_AMOUNT_MARKERS = (
    "amount",
    "base",
    "bonus",
    "deduction",
    "gross",
    "net",
    "rate",
    "currency",
    "salary",
    "note",
)


def _keys(obj: object) -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.append(key)
            found.extend(_keys(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_keys(item))
    return found


def test_payroll_calculated_envelope_and_run_key() -> None:
    envelope = payroll_calculated(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        run_id=RUN,
        period="2026-09",
        employee_count=12,
    )

    EventEnvelope.model_validate(envelope)
    assert envelope["event_type"] == "payroll.calculated"
    assert envelope["version"] == 1
    assert envelope["data"] == {
        "run_id": str(RUN),
        "period": "2026-09",
        "employee_count": 12,
    }
    assert kafka_message_key(envelope) == str(RUN)
    assert not [key for key in _keys(envelope) if key in _AMOUNT_MARKERS]


def test_payslip_issued_envelope_and_employee_key() -> None:
    envelope = payslip_issued(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        payslip_id=PAYSLIP,
        run_id=RUN,
        employee_id=EMPLOYEE,
        period="2026-09",
    )

    EventEnvelope.model_validate(envelope)
    assert envelope["event_type"] == "payslip.issued"
    assert envelope["data"] == {
        "payslip_id": str(PAYSLIP),
        "run_id": str(RUN),
        "employee_id": str(EMPLOYEE),
        "period": "2026-09",
    }
    assert kafka_message_key(envelope) == str(EMPLOYEE)
    assert not [key for key in _keys(envelope) if key in _AMOUNT_MARKERS]
