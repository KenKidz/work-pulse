"""Documents failure paths over HTTP (T5).

The happy paths live in `test_documents_api.py`; this suite pins every refusal
the feature spec promises (`.ai/features/employee-documents.md` → Error codes):

- `422 document_type_not_allowed` / `document_too_large` at intent time **and**
  re-checked at confirm time against the staged object;
- `409 document_upload_not_completed` for a missing staged object, a replay
  after a successful confirm, and missing protocol metadata;
- `404 document_not_found` after a delete (get/download/update/second delete);
- every refusal writes no row, no audit entry and no event.

Adversarial staged objects are written directly with boto3: the presigned POST
policy would normally refuse them, so confirm's re-check is the belt-and-braces
path the spec pins.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import documents as documents_api
from app.config import get_settings
from app.domains.documents.models import Document as DocumentRow
from app.domains.documents.schemas import MAX_DOCUMENT_SIZE_BYTES
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    identity,
)
from tests.integration.documents_helpers import (
    DEFAULT_CONTENT_TYPE,
    DEFAULT_FILENAME,
    PDF_BYTES,
    confirm_document,
    post_to_storage,
    put_staged_object,
    request_upload,
    seed_employee,
    upload_document,
)

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _hr() -> Any:
    return identity(role=Role.HR, employee_id=None)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


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


async def _assert_nothing_written(session: AsyncSession) -> None:
    """No document row, no *document* audit entry and no event."""
    assert (await session.execute(select(DocumentRow))).scalars().all() == []
    document_audit = (
        (
            await session.execute(
                select(AuditEntry).where(AuditEntry.entity_type == "document")
            )
        )
        .scalars()
        .all()
    )
    assert document_audit == []
    assert (await session.execute(select(EventOutboxRow))).scalars().all() == []


@pytest.mark.parametrize(
    ("content_type", "size_bytes", "code"),
    [
        ("text/html", len(PDF_BYTES), "document_type_not_allowed"),
        ("application/pdf", MAX_DOCUMENT_SIZE_BYTES + 1, "document_too_large"),
    ],
)
async def test_intent_rejects_disallowed_type_or_size(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    content_type: str,
    size_bytes: int,
    code: str,
) -> None:
    """AC2 — a bad claim is 422 and never yields a presigned URL."""
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        response = await request_upload(
            hr, ACTOR, content_type=content_type, size_bytes=size_bytes
        )

    _assert_error(response, 422, code)
    await _assert_nothing_written(session)


async def test_confirm_rechecks_the_staged_content_type(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staged object whose MIME type is outside the allowlist is `422`."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        intent = await request_upload(hr, ACTOR)
        upload_id = UUID(intent.json()["upload_id"])
        key = put_staged_object(
            document_bucket,
            upload_id,
            tenant_id=TENANT_A,
            content=PDF_BYTES,
            content_type="text/html",
            filename=DEFAULT_FILENAME,
            claimed_size=len(PDF_BYTES),
        )
        response = await confirm_document(hr, ACTOR, str(upload_id))

    _assert_error(response, 422, "document_type_not_allowed")
    # A refused confirm never moves or purges the staged object.
    assert document_bucket.head_object(Bucket=get_settings().storage_bucket, Key=key)
    await _assert_nothing_written(session)


async def test_confirm_rechecks_the_staged_size_against_the_claim(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staged object larger than the claim is `422 document_too_large`."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        intent = await request_upload(hr, ACTOR, size_bytes=len(PDF_BYTES))
        upload_id = UUID(intent.json()["upload_id"])
        put_staged_object(
            document_bucket,
            upload_id,
            tenant_id=TENANT_A,
            content=PDF_BYTES + b"extra bytes beyond the claim",
            content_type=DEFAULT_CONTENT_TYPE,
            filename=DEFAULT_FILENAME,
            claimed_size=len(PDF_BYTES),
        )
        response = await confirm_document(hr, ACTOR, str(upload_id))

    _assert_error(response, 422, "document_too_large")
    await _assert_nothing_written(session)


@pytest.mark.parametrize(
    ("filename", "claimed_size"),
    [
        (None, len(PDF_BYTES)),
        (DEFAULT_FILENAME, None),
    ],
)
async def test_confirm_requires_the_protocol_metadata(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
    filename: str | None,
    claimed_size: int | None,
) -> None:
    """Missing `x-amz-meta-*` protocol fields are an incomplete upload (`409`)."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        intent = await request_upload(hr, ACTOR)
        upload_id = UUID(intent.json()["upload_id"])
        put_staged_object(
            document_bucket,
            upload_id,
            tenant_id=TENANT_A,
            content=PDF_BYTES,
            content_type=DEFAULT_CONTENT_TYPE,
            filename=filename,
            claimed_size=claimed_size,
        )
        response = await confirm_document(hr, ACTOR, str(upload_id))

    _assert_error(response, 409, "document_upload_not_completed")
    await _assert_nothing_written(session)


async def test_confirm_without_an_object_is_incomplete(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-uploaded `upload_id` is `409`, never a row or a 500."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        response = await confirm_document(
            hr, ACTOR, "00000000-0000-4000-8000-0000000000ff"
        )

    _assert_error(response, 409, "document_upload_not_completed")
    await _assert_nothing_written(session)


async def test_confirm_replay_after_success_is_incomplete(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The staging object is gone after the move, so a replay is `409`."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        intent = await request_upload(hr, ACTOR)
        upload = intent.json()
        assert (await post_to_storage(upload)).status_code == 204
        first = await confirm_document(hr, ACTOR, upload["upload_id"])
        assert first.status_code == 201, first.text

        replay = await confirm_document(hr, ACTOR, upload["upload_id"])

    _assert_error(replay, 409, "document_upload_not_completed")
    assert len((await session.execute(select(DocumentRow))).scalars().all()) == 1
    document_audit = (
        (
            await session.execute(
                select(AuditEntry).where(AuditEntry.entity_type == "document")
            )
        )
        .scalars()
        .all()
    )
    assert len(document_audit) == 1
    assert len((await session.execute(select(EventOutboxRow))).scalars().all()) == 1


async def test_deleted_document_is_404_on_every_operational_read(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After delete: get/download/update 404; a second delete is 404 too."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_actor(client_factory, session)
    async with client_factory(_hr()) as hr:
        created = await upload_document(hr, ACTOR)
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        base = f"/api/v1/employees/{ACTOR}/documents/{document_id}"
        assert (await hr.delete(base)).status_code == 204

        fetched = await hr.get(base)
        downloaded = await hr.get(f"{base}/download")
        updated = await hr.patch(base, json={"title": "Nope"})
        deleted_again = await hr.delete(base)

    _assert_error(fetched, 404, "document_not_found")
    _assert_error(downloaded, 404, "document_not_found")
    _assert_error(updated, 404, "document_not_found")
    _assert_error(deleted_again, 404, "document_not_found")

    # The failed calls wrote nothing further: one upload + one delete audit row.
    assert [
        row.action
        for row in (
            await session.execute(
                select(AuditEntry).where(AuditEntry.entity_type == "document")
            )
        )
        .scalars()
        .all()
    ] == ["document.uploaded", "document.deleted"]
    assert [
        row.event_type
        for row in (await session.execute(select(EventOutboxRow))).scalars().all()
    ] == ["document.uploaded", "document.deleted"]
