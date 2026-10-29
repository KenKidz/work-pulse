"""Documents tenant-isolation tests (T5, AC10).

Rows and storage keys are tenant-scoped. A foreign `employee_id` answers
`404 employee_not_found`; a document id reached through the foreign tenant's own
employee path answers `404 document_not_found`; neither path touches storage or
the owner's row. Tenant B is a real second tenant with its own org references
and employee, not just a different UUID in the identity.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import documents as documents_api
from app.config import get_settings
from app.domains.documents.models import Document as DocumentRow
from app.domains.documents.storage import document_key
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    identity,
)
from tests.integration.documents_helpers import (
    confirm_document,
    request_upload,
    seed_employee,
    upload_document,
)

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
OTHER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000081")
OTHER_USER = UUID("00000000-0000-4000-8000-000000000082")


def _hr_a() -> Any:
    return identity(role=Role.HR, employee_id=None)


def _admin_b() -> Any:
    return identity(
        tenant=TENANT_B, role=Role.ADMIN, user_id=OTHER_USER, employee_id=None
    )


def _object_exists(bucket_client: Any, key: str) -> bool:
    try:
        bucket_client.head_object(Bucket=get_settings().storage_bucket, Key=key)
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True


async def _seed_both_tenants(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin_a:
        refs_a = await create_org_refs(admin_a)
    await seed_employee(
        session,
        refs_a,
        employee_id=ACTOR,
        code="EMP-00009",
        email="self@example.test",
        first_name="Actor",
        last_name="Self",
    )

    async with client_factory(_admin_b()) as admin_b:
        refs_b = await create_org_refs(admin_b, suffix="-B")
    await seed_employee(
        session,
        refs_b,
        tenant_id=TENANT_B,
        employee_id=OTHER_EMPLOYEE,
        code="EMP-00081",
        email="other@example.test",
        first_name="Other",
        last_name="Tenant",
    )


async def test_cross_tenant_document_access_is_404_and_leaves_storage_alone(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10 — every cross-tenant path is 404; the object and row are untouched."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_both_tenants(client_factory, session)
    async with client_factory(_hr_a()) as hr_a:
        created = await upload_document(hr_a, ACTOR)
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]

    storage_key = document_key(TENANT_A, ACTOR, UUID(document_id))
    assert _object_exists(document_bucket, storage_key)

    async with client_factory(_admin_b()) as tenant_b:
        # A foreign employee id never leaks as 403.
        foreign_list = await tenant_b.get(f"/api/v1/employees/{ACTOR}/documents")
        foreign_upload = await request_upload(tenant_b, ACTOR)
        foreign_confirm = await confirm_document(
            tenant_b, ACTOR, "00000000-0000-4000-8000-0000000000ff"
        )

        # The same document id through tenant B's own employee is "missing".
        foreign_get = await tenant_b.get(
            f"/api/v1/employees/{OTHER_EMPLOYEE}/documents/{document_id}"
        )
        foreign_download = await tenant_b.get(
            f"/api/v1/employees/{OTHER_EMPLOYEE}/documents/{document_id}/download"
        )
        foreign_update = await tenant_b.patch(
            f"/api/v1/employees/{OTHER_EMPLOYEE}/documents/{document_id}",
            json={"title": "Hijacked"},
        )
        foreign_delete = await tenant_b.delete(
            f"/api/v1/employees/{OTHER_EMPLOYEE}/documents/{document_id}"
        )
        own_list = await tenant_b.get(f"/api/v1/employees/{OTHER_EMPLOYEE}/documents")

    for response in (foreign_list, foreign_upload, foreign_confirm):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "employee_not_found"
    for response in (
        foreign_get,
        foreign_download,
        foreign_update,
        foreign_delete,
    ):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "document_not_found"

    assert own_list.status_code == 200
    assert own_list.json()["items"] == []

    # Storage and the owning row are exactly as tenant A left them.
    assert _object_exists(document_bucket, storage_key)
    row = (
        await session.execute(
            select(DocumentRow).where(DocumentRow.id == UUID(document_id))
        )
    ).scalar_one()
    assert row.tenant_id == TENANT_A
    assert row.deleted_at is None
    assert row.title == "Signed contract"

    # Only tenant A's upload event exists; no cross-tenant producer ran.
    outbox = (await session.execute(select(EventOutboxRow))).scalars().all()
    assert [(event.event_type, event.tenant_id) for event in outbox] == [
        ("document.uploaded", TENANT_A)
    ]
    objects = (
        document_bucket.list_objects_v2(Bucket=get_settings().storage_bucket).get(
            "Contents"
        )
        or []
    )
    assert [item["Key"] for item in objects] == [storage_key]
