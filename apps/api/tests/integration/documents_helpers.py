"""Shared fixtures helpers for the Documents integration suites (T5).

The upload protocol is exercised against the real `minio-test` bucket: the API
returns a presigned POST, this module posts the bytes to it with a plain HTTP
client (the ASGI test client never reaches MinIO), then the suite confirms the
upload through the API. A `document_bucket` fixture empties the throwaway
bucket around each test (see `tests/integration/conftest.py`).

Synthetic bytes and names only (`.ai/testing.md` rule 6). Nothing here logs a
presigned URL, a filename or a storage key.
"""

from datetime import date
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domains.documents.storage import staging_key
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.integration.conftest import TENANT_A

PDF_BYTES = b"%PDF-1.4\n% WorkPulse document test fixture\n"
DEFAULT_CONTENT_TYPE = "application/pdf"
DEFAULT_FILENAME = "signed-contract.pdf"
DEFAULT_TITLE = "Signed contract"
DEFAULT_CATEGORY = "contract"


def _documents_url(employee_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/employees/{employee_id}/documents{suffix}"


async def request_upload(
    client: AsyncClient,
    employee_id: UUID,
    *,
    content_type: str = DEFAULT_CONTENT_TYPE,
    size_bytes: int = len(PDF_BYTES),
) -> Response:
    """Step 1 over HTTP: ask for a presigned staging POST."""
    return await client.post(
        _documents_url(employee_id, "/uploads"),
        json={"content_type": content_type, "size_bytes": size_bytes},
    )


async def post_to_storage(
    upload: dict[str, Any],
    *,
    content: bytes = PDF_BYTES,
    filename: str = DEFAULT_FILENAME,
    content_type: str = DEFAULT_CONTENT_TYPE,
) -> Response:
    """Step 2: browser-style POST straight to the presigned object-store URL.

    The filename travels percent-encoded in `x-amz-meta-filename` (ADR 0022);
    confirm reads it back from the object's HEAD metadata.
    """
    fields = dict(upload["fields"])
    fields["x-amz-meta-filename"] = quote(filename, safe="")
    async with httpx.AsyncClient(timeout=30) as storage:
        return await storage.post(
            upload["url"],
            data=fields,
            files={"file": (filename, content, content_type)},
        )


async def confirm_document(
    client: AsyncClient,
    employee_id: UUID,
    upload_id: str,
    *,
    title: str = DEFAULT_TITLE,
    category: str = DEFAULT_CATEGORY,
    expiry_date: str | None = None,
) -> Response:
    """Step 3 over HTTP: confirm the staged object and create the row."""
    body: dict[str, Any] = {
        "upload_id": upload_id,
        "title": title,
        "category": category,
    }
    if expiry_date is not None:
        body["expiry_date"] = expiry_date
    return await client.post(_documents_url(employee_id), json=body)


async def upload_document(
    client: AsyncClient,
    employee_id: UUID,
    *,
    title: str = DEFAULT_TITLE,
    category: str = DEFAULT_CATEGORY,
    expiry_date: str | None = None,
    content: bytes = PDF_BYTES,
    filename: str = DEFAULT_FILENAME,
    content_type: str = DEFAULT_CONTENT_TYPE,
) -> Response:
    """The whole two-step flow: intent → object store → confirm."""
    intent = await request_upload(
        client,
        employee_id,
        content_type=content_type,
        size_bytes=len(content),
    )
    assert intent.status_code == 201, intent.text
    upload = intent.json()
    staged = await post_to_storage(
        upload,
        content=content,
        filename=filename,
        content_type=content_type,
    )
    assert staged.status_code == 204, staged.text
    return await confirm_document(
        client,
        employee_id,
        upload["upload_id"],
        title=title,
        category=category,
        expiry_date=expiry_date,
    )


def put_staged_object(
    bucket_client: Any,
    upload_id: UUID,
    *,
    tenant_id: UUID = TENANT_A,
    content: bytes,
    content_type: str = DEFAULT_CONTENT_TYPE,
    filename: str | None = DEFAULT_FILENAME,
    claimed_size: int | None = None,
) -> str:
    """Write a staging object directly (for confirm re-check failure cases).

    The presigned policy would normally refuse these shapes; a direct PUT
    simulates a store that accepted an inconsistent object, which is exactly
    what confirm's belt-and-braces re-check exists for. Returns the key so a
    caller can assert it was not moved.
    """
    settings = get_settings()
    key = staging_key(tenant_id, upload_id)
    metadata: dict[str, str] = {}
    if filename is not None:
        metadata["filename"] = quote(filename, safe="")
    if claimed_size is not None:
        metadata["claimed-size"] = str(claimed_size)
    bucket_client.put_object(
        Bucket=settings.storage_bucket,
        Key=key,
        Body=content,
        ContentType=content_type,
        Metadata=metadata,
    )
    return key


async def seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    tenant_id: UUID = TENANT_A,
    employee_id: UUID,
    code: str,
    email: str,
    first_name: str,
    last_name: str,
    manager_id: UUID | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
) -> None:
    """Seed one employee row directly through the repository (no HTTP)."""
    await PostgresEmployeeRepository(session).add(
        tenant_id,
        employee_id,
        employee_code=code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=status,
        hired_at=date(2026, 1, 5),
    )
