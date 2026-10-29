"""HTTP integration tests for the Documents happy paths (T5).

Every test drives the real router, `DocumentService`, the PostgreSQL adapters
and the real private `minio-test` bucket: the suite asks for a presigned POST,
uploads the bytes to it with a plain HTTP client, then confirms through the API
— the same two-step protocol a browser uses.

Covered here (`.ai/features/employee-documents.md` → T5, AC 1/3/8/9):

- intent: 201, presigned POST pinned to the exact staging key, claim fields,
  upload TTL ≤ 10 minutes;
- confirm: 201 metadata matches the staged object, the object moved to
  `documents/{tenant}/{employee}/{document}`, exactly one audit entry and one
  identifiers-only outbox envelope;
- list: pagination clamps and the `category` filter;
- download: a fresh presigned GET that actually returns the stored bytes;
- update: 200 + `document.updated` audit and **no** outbox row;
- delete: 204, object purged, `deleted_at` set, every read 404, audit + event,
  second delete 404.

PII values used here are synthetic. Presigned URLs are never logged or asserted
into audit/outbox payloads.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from botocore.exceptions import ClientError
from httpx import Response
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import documents as documents_api
from app.config import get_settings
from app.domains.documents.models import Document as DocumentRow
from app.domains.documents.storage import document_key, staging_key
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
from tests.integration.documents_helpers import (
    DEFAULT_CATEGORY,
    DEFAULT_CONTENT_TYPE,
    DEFAULT_FILENAME,
    DEFAULT_TITLE,
    PDF_BYTES,
    confirm_document,
    post_to_storage,
    request_upload,
    seed_employee,
    upload_document,
)

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "documents"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

PINNED_DOCUMENT_KEYS = {
    "id",
    "employee_id",
    "title",
    "category",
    "expiry_date",
    "filename",
    "content_type",
    "size_bytes",
    "uploaded_by",
    "created_at",
    "updated_at",
}


def _hr() -> Any:
    return identity(role=Role.HR, employee_id=None)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


def _object(bucket_client: Any, key: str) -> dict[str, Any] | None:
    """HEAD one object; `None` when it does not exist."""
    try:
        return bucket_client.head_object(Bucket=get_settings().storage_bucket, Key=key)
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


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


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


async def _document_rows(session: AsyncSession) -> list[DocumentRow]:
    result = await session.execute(
        select(DocumentRow).order_by(DocumentRow.created_at, DocumentRow.id)
    )
    return list(result.scalars().all())


async def test_upload_intent_returns_presigned_post_for_the_staging_key(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
) -> None:
    """AC1 — 201 with a POST pinned to `staging/{tenant}/{upload}` and a claim."""
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        response = await request_upload(hr, ACTOR)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"upload_id", "url", "fields", "expires_at"}
    upload_id = UUID(body["upload_id"])
    expected_key = staging_key(TENANT_A, upload_id)
    assert body["fields"]["key"] == expected_key
    assert body["fields"]["Content-Type"] == DEFAULT_CONTENT_TYPE
    assert body["fields"]["x-amz-meta-claimed-size"] == str(len(PDF_BYTES))
    assert body["url"].startswith(get_settings().storage_endpoint)

    expires_at = datetime.fromisoformat(body["expires_at"])
    remaining = expires_at - datetime.now(UTC)
    assert timedelta(0) < remaining <= timedelta(minutes=10, seconds=1)

    # The intent creates no row and no object: only the client's next POST does.
    assert await _document_rows(session) == []
    assert _object(document_bucket, expected_key) is None


async def test_confirm_moves_the_object_creates_the_row_and_emits_ids_only(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 — the full two-step flow, its audit entry and its event envelope."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        intent = await request_upload(hr, ACTOR)
        assert intent.status_code == 201, intent.text
        upload = intent.json()
        staged = await post_to_storage(upload)
        assert staged.status_code == 204, staged.text
        response = await confirm_document(
            hr, ACTOR, upload["upload_id"], expiry_date="2027-12-31"
        )

    assert response.status_code == 201, response.text
    document = response.json()
    assert set(document) == PINNED_DOCUMENT_KEYS
    assert document["employee_id"] == str(ACTOR)
    assert document["title"] == DEFAULT_TITLE
    assert document["category"] == DEFAULT_CATEGORY
    assert document["expiry_date"] == "2027-12-31"
    assert document["filename"] == DEFAULT_FILENAME
    assert document["content_type"] == DEFAULT_CONTENT_TYPE
    assert document["size_bytes"] == len(PDF_BYTES)
    assert document["uploaded_by"] == str(USER)
    assert datetime.fromisoformat(document["created_at"]) == CLOCK
    assert datetime.fromisoformat(document["updated_at"]) == CLOCK

    # Staging is gone; the final object holds the same bytes and metadata.
    upload_id = UUID(upload["upload_id"])
    final_key = document_key(TENANT_A, ACTOR, UUID(document["id"]))
    staged_key = staging_key(TENANT_A, upload_id)
    assert _object(document_bucket, staged_key) is None
    stored = _object(document_bucket, final_key)
    assert stored is not None
    assert stored["ContentLength"] == len(PDF_BYTES)
    assert stored["ContentType"] == DEFAULT_CONTENT_TYPE

    rows = await _document_rows(session)
    assert len(rows) == 1
    assert rows[0].id == UUID(document["id"])
    assert rows[0].deleted_at is None

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["document.uploaded"]
    assert audit[0].entity_id == UUID(document["id"])
    upload_changes = audit[0].changes or {}
    assert upload_changes["title"] == "changed"
    assert upload_changes["filename"] == "changed"
    assert upload_changes["category"] == {"old": None, "new": DEFAULT_CATEGORY}
    assert upload_changes["size_bytes"] == {"old": None, "new": len(PDF_BYTES)}

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == ["document.uploaded"]
    envelope = outbox[0].payload
    assert envelope["data"] == {
        "document_id": document["id"],
        "employee_id": str(ACTOR),
        "category": DEFAULT_CATEGORY,
    }
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["actor_id"] == str(USER)
    assert datetime.fromisoformat(envelope["occurred_at"]) == CLOCK

    # The persisted envelope validates against the frozen contracts.
    Draft202012Validator(_load(ENVELOPE_PATH)).validate(envelope)
    Draft202012Validator(_load(SCHEMAS_DIR / "document-uploaded.v1.json")).validate(
        envelope["data"]
    )

    # No PII leaked into the payload.
    payload_dump = json.dumps(envelope)
    assert DEFAULT_TITLE not in payload_dump
    assert DEFAULT_FILENAME not in payload_dump
    assert final_key not in payload_dump


async def test_list_filters_by_category_and_clamps_pagination(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pinned list envelope: visibility first, category filter, clamp."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        first = await upload_document(hr, ACTOR, title="Contract")
        second = await upload_document(
            hr, ACTOR, title="Certificate", category="certification"
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        shown = await hr.get(
            f"/api/v1/employees/{ACTOR}/documents",
            params={"page": 0, "page_size": 1000},
        )
        filtered = await hr.get(
            f"/api/v1/employees/{ACTOR}/documents",
            params={"category": "certification"},
        )

    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["page"] == {
        "page": 1,
        "page_size": 100,
        "total_items": 2,
        "total_pages": 1,
    }
    assert {item["id"] for item in body["items"]} == {
        first.json()["id"],
        second.json()["id"],
    }

    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert [item["id"] for item in filtered_body["items"]] == [second.json()["id"]]
    assert filtered_body["page"]["total_items"] == 1


async def test_download_returns_a_working_fresh_presigned_get(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh presigned GET per call; the URL actually returns the bytes."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        created = await upload_document(hr, ACTOR)
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        first = await hr.get(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}/download"
        )
        second = await hr.get(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}/download"
        )

    for response in (first, second):
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"url", "expires_at"}
        expires_at = datetime.fromisoformat(body["expires_at"])
        remaining = expires_at - datetime.now(UTC)
        assert timedelta(0) < remaining <= timedelta(minutes=5, seconds=1)

    async with httpx.AsyncClient(timeout=30) as storage:
        downloaded = await storage.get(first.json()["url"])
    assert downloaded.status_code == 200
    assert downloaded.content == PDF_BYTES

    # The presigned URL is never stored in audit or event rows.
    audit_dump = json.dumps([row.changes for row in await _audit_rows(session)])
    outbox_dump = json.dumps([row.payload for row in await _outbox_rows(session)])
    assert first.json()["url"] not in audit_dump
    assert first.json()["url"] not in outbox_dump


async def test_update_metadata_is_audited_without_an_event(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC9 — a field update: 200, `document.updated`, no new outbox row."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        created = await upload_document(hr, ACTOR)
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        updated = await hr.patch(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}",
            json={
                "title": "Renamed contract",
                "category": "identification",
                "expiry_date": "2028-01-31",
            },
        )
        fetched = await hr.get(f"/api/v1/employees/{ACTOR}/documents/{document_id}")

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["title"] == "Renamed contract"
    assert body["category"] == "identification"
    assert body["expiry_date"] == "2028-01-31"
    assert datetime.fromisoformat(body["updated_at"]) == CLOCK
    assert fetched.json()["title"] == "Renamed contract"

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["document.uploaded", "document.updated"]
    update_changes = audit[1].changes or {}
    assert update_changes["title"] == "changed"
    assert update_changes["category"] == {
        "old": "contract",
        "new": "identification",
    }
    assert update_changes["expiry_date"] == {"old": None, "new": "2028-01-31"}

    # Field update, not a state transition: exactly the upload's event remains.
    assert [row.event_type for row in await _outbox_rows(session)] == [
        "document.uploaded"
    ]


async def test_delete_purges_the_object_and_hides_every_read(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8 — 204, object gone, `deleted_at` set, reads 404, audit + event."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        created = await upload_document(hr, ACTOR)
        assert created.status_code == 201, created.text
        document_id = UUID(created.json()["id"])
        final_key = document_key(TENANT_A, ACTOR, document_id)
        assert _object(document_bucket, final_key) is not None

        deleted = await hr.delete(f"/api/v1/employees/{ACTOR}/documents/{document_id}")
        listed = await hr.get(f"/api/v1/employees/{ACTOR}/documents")
        fetched = await hr.get(f"/api/v1/employees/{ACTOR}/documents/{document_id}")
        downloaded = await hr.get(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}/download"
        )
        second_delete = await hr.delete(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}"
        )

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert _object(document_bucket, final_key) is None
    assert listed.json()["items"] == []
    _assert_error(fetched, 404, "document_not_found")
    _assert_error(downloaded, 404, "document_not_found")
    _assert_error(second_delete, 404, "document_not_found")

    rows = await _document_rows(session)
    assert len(rows) == 1
    assert rows[0].deleted_at == CLOCK

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["document.uploaded", "document.deleted"]
    assert (audit[1].changes or {})["deleted_at"] == {
        "old": None,
        "new": CLOCK.isoformat(),
    }

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "document.uploaded",
        "document.deleted",
    ]
    assert outbox[1].payload["data"] == {
        "document_id": str(document_id),
        "employee_id": str(ACTOR),
        "category": DEFAULT_CATEGORY,
    }
    Draft202012Validator(_load(SCHEMAS_DIR / "document-deleted.v1.json")).validate(
        outbox[1].payload["data"]
    )


async def test_confirm_without_a_staged_object_writes_nothing(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 — `409 document_upload_not_completed`, zero rows/audit/event."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        response = await confirm_document(
            hr,
            ACTOR,
            "00000000-0000-4000-8000-0000000000ff",
        )

    _assert_error(response, 409, "document_upload_not_completed")
    assert await _document_rows(session) == []
    assert await _audit_rows(session) == []
    assert await _outbox_rows(session) == []
