"""Unit test for the audit entry shape — exactly the pinned fields, no PII slots."""

from datetime import UTC, datetime
from uuid import uuid4

from app.shared.audit import AuditEntryCreate

PINNED_FIELDS = {
    "tenant_id",
    "actor_id",
    "action",
    "entity_type",
    "entity_id",
    "occurred_at",
    "reason",
    "changes",
}


def test_entry_carries_exactly_the_pinned_fields() -> None:
    assert set(AuditEntryCreate.model_fields) == PINNED_FIELDS


def test_entry_serializes_with_all_pinned_fields() -> None:
    entry = AuditEntryCreate(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        action="department.created",
        entity_type="department",
        entity_id=uuid4(),
        occurred_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
        reason="reorg",
        changes={"name": {"old": None, "new": "Engineering"}},
    )

    dumped = entry.model_dump()

    assert set(dumped) == PINNED_FIELDS
    assert dumped["action"] == "department.created"
    assert dumped["reason"] == "reorg"


def test_optional_fields_default_to_none() -> None:
    entry = AuditEntryCreate(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        action="position.updated",
        entity_type="position",
        entity_id=uuid4(),
        occurred_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
    )

    assert entry.reason is None
    assert entry.changes is None
