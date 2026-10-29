"""Documents audit + PII sweep (T5).

One flow drives upload → metadata update → delete, then inspects the
append-only audit rows, the persisted outbox envelopes and captured logs:

- exactly one audit entry per mutation (`document.uploaded`,
  `document.updated`, `document.deleted`);
- `title` and `filename` appear only as the `"changed"` marker — never as a
  value; category/expiry show old → new codes, never free text;
- event payloads carry `document_id`, `employee_id`, `category` only;
- no concrete title, filename, storage key or presigned URL reaches audit,
  events or logs (`.ai/security.md`).

A metadata update is a field update, not a state transition: it is audited and
emits **no** event.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import documents as documents_api
from app.domains.documents.models import Document as DocumentRow
from app.domains.documents.storage import document_key
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)
from tests.integration.documents_helpers import seed_employee, upload_document

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

PII_TITLE = "Secret contract — pii-document-title"
PII_TITLE_UPDATED = "Renamed secret — pii-document-title-2"
PII_FILENAME = "private-passport-scan.pdf"
PII_VALUES = (PII_TITLE, PII_TITLE_UPDATED, PII_FILENAME)

PII_CHANGE_KEYS = frozenset({"title", "filename"})
EVENT_DATA_KEYS = frozenset({"document_id", "employee_id", "category"})


def _hr() -> Any:
    return identity(role=Role.HR, employee_id=None)


async def _seed_actor(client_factory: ClientFactory, session: AsyncSession) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await seed_employee(
        session,
        refs,
        employee_id=ACTOR,
        code="EMP-00009",
        email="self@example.test",
        first_name="Actor",
        last_name="Self",
    )


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "document")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def test_documents_are_audited_without_pii(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    structlog.configure(cache_logger_on_first_use=False)

    with structlog.testing.capture_logs() as logs:
        async with client_factory(_hr()) as hr:
            created = await upload_document(
                hr,
                ACTOR,
                title=PII_TITLE,
                filename=PII_FILENAME,
            )
            assert created.status_code == 201, created.text
            document_id = created.json()["id"]
            base = f"/api/v1/employees/{ACTOR}/documents/{document_id}"
            updated = await hr.patch(
                base,
                json={
                    "title": PII_TITLE_UPDATED,
                    "category": "certification",
                    "expiry_date": "2028-06-30",
                },
            )
            assert updated.status_code == 200, updated.text
            deleted = await hr.delete(base)
            assert deleted.status_code == 204

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "document.uploaded",
        "document.updated",
        "document.deleted",
    ]
    assert all(row.entity_id == UUID(document_id) for row in audit)
    assert all(row.tenant_id == TENANT_A for row in audit)
    assert all(row.actor_id == USER for row in audit)

    upload_changes = audit[0].changes or {}
    assert upload_changes["title"] == "changed"
    assert upload_changes["filename"] == "changed"
    assert upload_changes["category"] == {"old": None, "new": "contract"}

    update_changes = audit[1].changes or {}
    assert update_changes["title"] == "changed"
    assert update_changes["category"] == {
        "old": "contract",
        "new": "certification",
    }
    assert update_changes["expiry_date"] == {"old": None, "new": "2028-06-30"}

    delete_changes = audit[2].changes or {}
    assert delete_changes["deleted_at"] == {
        "old": None,
        "new": CLOCK.isoformat(),
    }

    # PII keys only ever carry the marker; no concrete value is anywhere.
    for row in audit:
        for key, value in (row.changes or {}).items():
            if key in PII_CHANGE_KEYS:
                assert value == "changed", f"{key} leaked: {value!r}"

    audit_dump = json.dumps([row.changes for row in audit], default=str)
    outbox = (
        (await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id)))
        .scalars()
        .all()
    )
    outbox_dump = json.dumps([row.payload for row in outbox], default=str)
    storage_key = document_key(TENANT_A, ACTOR, UUID(document_id))
    logs_dump = json.dumps(logs, default=str)
    for dump in (audit_dump, outbox_dump, logs_dump):
        for value in PII_VALUES:
            assert value not in dump
        assert storage_key not in dump

    # Two events only (upload + delete); the metadata update emits nothing.
    assert [row.event_type for row in outbox] == [
        "document.uploaded",
        "document.deleted",
    ]
    for row in outbox:
        assert set(row.payload["data"]) == EVENT_DATA_KEYS
        assert row.payload["data"]["document_id"] == document_id
        assert row.payload["data"]["employee_id"] == str(ACTOR)
        assert row.payload["data"]["category"] in {
            "contract",
            "identification",
            "certification",
            "other",
        }
        assert row.payload["tenant_id"] == str(TENANT_A)
        assert row.payload["actor_id"] == str(USER)

    # The row is kept (soft delete) for traceability; the bytes are not.
    row = (
        await session.execute(
            select(DocumentRow).where(DocumentRow.id == UUID(document_id))
        )
    ).scalar_one()
    assert row.deleted_at == CLOCK
    assert logs, "expected the request middleware to log each call"
