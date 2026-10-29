"""Documents role matrix over HTTP (T5).

Pins `.ai/features/employee-documents.md` → *Roles & access* and acceptance
criteria 5–7: writes are HR / Admin / Super Admin only; reads are the owner
(any role), a manager over self + direct reports (one level), HR / Admin /
Super Admin everyone and Finance their own only. A same-tenant out-of-scope
read is `403 forbidden`; a foreign or unknown employee id is `404
employee_not_found`; a document id used through another employee's path is
`404 document_not_found`.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import documents as documents_api
from app.domains.documents.models import Document as DocumentRow
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
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

MANAGER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000021")
REPORT_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000022")
PEER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000023")
UNKNOWN_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000ee")
UNKNOWN_DOCUMENT = UUID("00000000-0000-4000-8000-0000000000ff")

MANAGER_USER = UUID("00000000-0000-4000-8000-000000000041")
FINANCE_USER = UUID("00000000-0000-4000-8000-000000000042")


def _hr() -> Any:
    return identity(role=Role.HR, employee_id=None)


def _admin() -> Any:
    return identity(role=Role.ADMIN, employee_id=None)


def _super_admin() -> Any:
    return identity(role=Role.SUPER_ADMIN, employee_id=None)


def _employee(employee_id: UUID = ACTOR) -> Any:
    return identity(role=Role.EMPLOYEE, employee_id=employee_id)


def _manager() -> Any:
    return identity(
        role=Role.MANAGER, user_id=MANAGER_USER, employee_id=MANAGER_EMPLOYEE
    )


def _finance() -> Any:
    return identity(role=Role.FINANCE, user_id=FINANCE_USER, employee_id=ACTOR)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
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
    await seed_employee(
        session,
        refs,
        employee_id=MANAGER_EMPLOYEE,
        code="EMP-00021",
        email="manager@example.test",
        first_name="Morgan",
        last_name="Manager",
    )
    await seed_employee(
        session,
        refs,
        employee_id=REPORT_EMPLOYEE,
        code="EMP-00022",
        email="report@example.test",
        first_name="Riley",
        last_name="Report",
        manager_id=MANAGER_EMPLOYEE,
    )
    await seed_employee(
        session,
        refs,
        employee_id=PEER_EMPLOYEE,
        code="EMP-00023",
        email="peer@example.test",
        first_name="Parker",
        last_name="Peer",
    )


async def _documents_by_employee(
    client_factory: ClientFactory,
) -> dict[UUID, str]:
    """One document per employee, created by HR; returns employee → document id."""
    created: dict[UUID, str] = {}
    async with client_factory(_hr()) as hr:
        for employee_id in (
            ACTOR,
            MANAGER_EMPLOYEE,
            REPORT_EMPLOYEE,
            PEER_EMPLOYEE,
        ):
            response = await upload_document(
                hr, employee_id, title=f"Record {employee_id}"
            )
            assert response.status_code == 201, response.text
            created[employee_id] = response.json()["id"]
    return created


async def test_non_management_roles_cannot_write(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5 — Employee, Manager and Finance get `403 forbidden` on every write."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    documents = await _documents_by_employee(client_factory)
    document_id = documents[ACTOR]

    baseline_audit = len((await session.execute(select(AuditEntry))).scalars().all())
    baseline_outbox = len(
        (await session.execute(select(EventOutboxRow))).scalars().all()
    )

    for actor in (_employee(), _manager(), _finance()):
        async with client_factory(actor) as client:
            intent = await request_upload(client, ACTOR)
            confirm = await confirm_document(client, ACTOR, str(UNKNOWN_DOCUMENT))
            update = await client.patch(
                f"/api/v1/employees/{ACTOR}/documents/{document_id}",
                json={"title": "Hijacked"},
            )
            delete = await client.delete(
                f"/api/v1/employees/{ACTOR}/documents/{document_id}"
            )

        _assert_error(intent, 403, "forbidden")
        _assert_error(confirm, 403, "forbidden")
        _assert_error(update, 403, "forbidden")
        _assert_error(delete, 403, "forbidden")

    assert len((await session.execute(select(AuditEntry))).scalars().all()) == (
        baseline_audit
    )
    assert (
        len((await session.execute(select(EventOutboxRow))).scalars().all())
        == baseline_outbox
    )
    # The row survives and only its own uploader's action exists.
    row = (
        await session.execute(
            select(DocumentRow).where(DocumentRow.id == UUID(document_id))
        )
    ).scalar_one()
    assert row.title == f"Record {ACTOR}"
    assert row.deleted_at is None


@pytest.mark.parametrize(
    "actor_factory", [_hr, _admin, _super_admin], ids=["hr", "admin", "super_admin"]
)
async def test_management_roles_can_manage(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
    actor_factory: Any,
) -> None:
    """HR / Admin / Super Admin may upload, update and delete."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    async with client_factory(actor_factory()) as client:
        created = await upload_document(client, ACTOR)
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        updated = await client.patch(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}",
            json={"title": "Offboarding record"},
        )
        deleted = await client.delete(
            f"/api/v1/employees/{ACTOR}/documents/{document_id}"
        )

    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Offboarding record"
    assert deleted.status_code == 204


async def test_read_scope_matrix(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6 — owner, manager over self + direct reports, HR/Admin everywhere."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    documents = await _documents_by_employee(client_factory)

    async def _list(actor: Any, employee_id: UUID) -> Response:
        async with client_factory(actor) as client:
            return await client.get(f"/api/v1/employees/{employee_id}/documents")

    # Owner (any role) and manager scope (self + direct reports, one level).
    assert (await _list(_employee(), ACTOR)).status_code == 200
    assert (await _list(_manager(), MANAGER_EMPLOYEE)).status_code == 200
    assert (await _list(_manager(), REPORT_EMPLOYEE)).status_code == 200
    _assert_error(await _list(_manager(), PEER_EMPLOYEE), 403, "forbidden")
    _assert_error(await _list(_employee(), PEER_EMPLOYEE), 403, "forbidden")

    # Finance: own only.
    assert (await _list(_finance(), ACTOR)).status_code == 200
    _assert_error(await _list(_finance(), REPORT_EMPLOYEE), 403, "forbidden")

    # Management roles: everyone.
    for actor in (_hr(), _admin(), _super_admin()):
        for employee_id, document_id in documents.items():
            response = await _list(actor, employee_id)
            assert response.status_code == 200, response.text
            assert [item["id"] for item in response.json()["items"]] == [document_id]

    # Unknown/foreign employee ids are 404, never a leaky 403.
    _assert_error(await _list(_employee(), UNKNOWN_EMPLOYEE), 404, "employee_not_found")


async def test_denied_download_returns_no_presigned_url(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_bucket: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7 — an out-of-scope download is 403 and mints no URL."""
    monkeypatch.setattr(documents_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    documents = await _documents_by_employee(client_factory)
    peer_document = documents[PEER_EMPLOYEE]
    report_document = documents[REPORT_EMPLOYEE]

    async with client_factory(_employee()) as employee:
        denied = await employee.get(
            f"/api/v1/employees/{PEER_EMPLOYEE}/documents/{peer_document}/download"
        )
        own_path = await employee.get(
            f"/api/v1/employees/{ACTOR}/documents/{peer_document}"
        )
    async with client_factory(_manager()) as manager:
        allowed = await manager.get(
            f"/api/v1/employees/{REPORT_EMPLOYEE}/documents/{report_document}/download"
        )

    _assert_error(denied, 403, "forbidden")
    # The nested route binds the document to its employee: no cross-employee read.
    _assert_error(own_path, 404, "document_not_found")
    assert allowed.status_code == 200, allowed.text
    assert set(allowed.json()) == {"url", "expires_at"}
