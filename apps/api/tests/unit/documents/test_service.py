"""Unit tests for `DocumentService` (T4).

No database and no object store: the fakes in `fakes.py` implement the ports
with the same tenant/employee scope, soft-delete visibility and conditional
write rules as the Postgres adapter. The tests pin the behaviors the spec
assigns to the service layer:

- upload intent validates the claim before minting a URL (AC1/AC2);
- confirm re-checks the staged object, copies then purges, and writes the row,
  audit entry and identifiers-only event together (AC3/AC4);
- denied writes stop before any storage or database write (AC5);
- reads resolve the manager/owner scope first, then 404 for unknown/deleted
  rows (AC6/AC7);
- delete purges first, then soft-deletes, and a second delete is 404 (AC8);
- metadata edits audit `document.updated` and emit no event (AC9);
- cross-tenant reads are `404` and storage is untouched (AC10).
"""

from collections.abc import Mapping
from datetime import date
from uuid import UUID

import pytest

from app.domains.documents.exceptions import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    DocumentTypeNotAllowedError,
    DocumentUploadNotCompletedError,
    EmployeeNotFoundError,
)
from app.domains.documents.schemas import (
    MAX_DOCUMENT_SIZE_BYTES,
    Document,
    DocumentCategory,
    DocumentCreate,
    DocumentUpdate,
    DocumentUploadCreate,
)
from app.domains.documents.service import PII_CHANGED, DocumentService
from app.domains.documents.storage import (
    ObjectMissingError,
    document_key,
    staging_key,
)
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from app.shared.outbox import kafka_message_key
from app.shared.pagination import PageParams
from tests.unit.documents.fakes import (
    DOCUMENT_ID,
    DOCUMENT_ID_2,
    NOW,
    OTHER,
    PEER,
    REPORT,
    SELF,
    TENANT_A,
    TENANT_B,
    UPLOAD_ID,
    USER,
    FakeDocumentRepo,
    FakeDocumentStorage,
    FakeEmployeeScope,
    identity,
    object_stat,
    staged_metadata,
)
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

UNKNOWN_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000ff")


@pytest.fixture
def journal() -> list[str]:
    return []


@pytest.fixture
def repo(journal: list[str]) -> FakeDocumentRepo:
    return FakeDocumentRepo(journal=journal)


@pytest.fixture
def storage(journal: list[str]) -> FakeDocumentStorage:
    return FakeDocumentStorage(journal=journal)


@pytest.fixture
def directory() -> FakeEmployeeScope:
    scope = FakeEmployeeScope()
    scope.add(TENANT_A, SELF)
    scope.add(TENANT_A, REPORT, manager_id=SELF)
    scope.add(TENANT_A, PEER)
    scope.add(TENANT_B, OTHER)
    return scope


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


@pytest.fixture
def service(
    repo: FakeDocumentRepo,
    storage: FakeDocumentStorage,
    directory: FakeEmployeeScope,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> DocumentService:
    return DocumentService(
        repo=repo,
        storage=storage,
        employees=directory,
        scope=directory,
        audit=audit,
        outbox=outbox,
    )


def _staged(
    storage: FakeDocumentStorage,
    *,
    tenant_id: UUID = TENANT_A,
    upload_id: UUID = UPLOAD_ID,
    size_bytes: int = 1024,
    content_type: str = "application/pdf",
    filename: str = "contract.pdf",
    claimed_size: int | None = None,
    metadata: Mapping[str, str] | None = None,
) -> str:
    """Put one object at the staging key; return the key."""
    key = staging_key(tenant_id, upload_id)
    meta = (
        dict(metadata)
        if metadata is not None
        else staged_metadata(
            filename=filename, size_bytes=size_bytes, claimed_size=claimed_size
        )
    )
    storage.objects[key] = object_stat(
        size_bytes=size_bytes, content_type=content_type, metadata=meta
    )
    return key


def _confirm(**overrides: object) -> DocumentCreate:
    values: dict[str, object] = {
        "upload_id": UPLOAD_ID,
        "title": "Signed contract",
        "category": DocumentCategory.CONTRACT,
    }
    values.update(overrides)
    return DocumentCreate.model_validate(values)


async def _seed_document(
    repo: FakeDocumentRepo,
    *,
    document_id: UUID = DOCUMENT_ID,
    tenant_id: UUID = TENANT_A,
    employee_id: UUID = SELF,
    title: str = "Signed contract",
    category: DocumentCategory = DocumentCategory.CONTRACT,
    expiry_date: date | None = None,
) -> Document:
    return await repo.create(
        tenant_id,
        document_id,
        employee_id=employee_id,
        title=title,
        category=category,
        expiry_date=expiry_date,
        filename="contract.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        uploaded_by=USER,
        now=NOW,
    )


# --- upload intent -----------------------------------------------------------


async def test_create_upload_presigns_the_staging_key(
    service: DocumentService, storage: FakeDocumentStorage
) -> None:
    upload = await service.create_upload(
        identity(Role.HR),
        SELF,
        DocumentUploadCreate(content_type="application/pdf", size_bytes=1024),
    )

    key = staging_key(TENANT_A, upload.upload_id)
    assert storage.uploads == [(TENANT_A, upload.upload_id, "application/pdf", 1024)]
    assert upload.url == f"https://minio.test/{key}"
    assert upload.fields["key"] == key
    assert "contract" not in upload.url


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "image/gif", "application/pdf; charset=utf-8"],
)
async def test_create_upload_rejects_a_type_outside_the_allowlist(
    service: DocumentService, storage: FakeDocumentStorage, content_type: str
) -> None:
    with pytest.raises(DocumentTypeNotAllowedError):
        await service.create_upload(
            identity(Role.HR),
            SELF,
            DocumentUploadCreate(content_type=content_type, size_bytes=1024),
        )
    assert storage.uploads == []


async def test_create_upload_rejects_a_size_over_the_cap(
    service: DocumentService, storage: FakeDocumentStorage
) -> None:
    with pytest.raises(DocumentTooLargeError):
        await service.create_upload(
            identity(Role.HR),
            SELF,
            DocumentUploadCreate(
                content_type="application/pdf",
                size_bytes=MAX_DOCUMENT_SIZE_BYTES + 1,
            ),
        )
    assert storage.uploads == []


async def test_create_upload_requires_an_employee_in_the_tenant(
    service: DocumentService, storage: FakeDocumentStorage
) -> None:
    with pytest.raises(EmployeeNotFoundError):
        await service.create_upload(
            identity(Role.HR),
            OTHER,  # exists only in TENANT_B
            DocumentUploadCreate(content_type="application/pdf", size_bytes=1024),
        )
    assert storage.uploads == []


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_create_upload_denied_for_non_management(
    service: DocumentService, storage: FakeDocumentStorage, role: Role
) -> None:
    with pytest.raises(ForbiddenError):
        await service.create_upload(
            identity(role),
            SELF,
            DocumentUploadCreate(content_type="application/pdf", size_bytes=1024),
        )
    assert storage.uploads == []


# --- confirm -----------------------------------------------------------------


async def test_confirm_copies_purges_and_writes_row_audit_and_event(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    journal: list[str],
) -> None:
    staged = _staged(storage, filename="Hợp đồng.pdf")

    document = await service.create(
        identity(Role.HR),
        SELF,
        _confirm(expiry_date=date(2027, 1, 31)),
        NOW,
    )

    final = document_key(TENANT_A, SELF, document.id)
    assert document.filename == "Hợp đồng.pdf"
    assert document.content_type == "application/pdf"
    assert document.size_bytes == 1024
    assert document.uploaded_by == USER
    assert document.employee_id == SELF
    assert document.expiry_date == date(2027, 1, 31)
    assert staged not in storage.objects
    assert final in storage.objects
    assert await repo.get(TENANT_A, SELF, document.id) == document
    assert journal == ["storage.copy", "storage.delete", "repo.create"]

    [entry] = audit.entries
    assert entry.action == "document.uploaded"
    assert entry.entity_type == "document"
    assert entry.entity_id == document.id
    assert entry.actor_id == USER
    changes = entry.changes
    assert changes is not None
    assert changes["title"] == PII_CHANGED
    assert changes["filename"] == PII_CHANGED
    assert changes["category"] == {"old": None, "new": "contract"}
    assert changes["expiry_date"] == {"old": None, "new": "2027-01-31"}
    rendered = repr(changes)
    assert "Signed contract" not in rendered
    assert "Hợp đồng.pdf" not in rendered

    [envelope] = outbox.envelopes
    assert envelope["event_id"].startswith("evt_")
    assert envelope["event_type"] == "document.uploaded"
    assert envelope["version"] == 1
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["actor_id"] == str(USER)
    assert envelope["occurred_at"] == NOW.isoformat()
    assert envelope["data"] == {
        "document_id": str(document.id),
        "employee_id": str(SELF),
        "category": "contract",
    }
    assert kafka_message_key(envelope) == str(SELF)
    rendered_event = repr(envelope)
    assert "Signed contract" not in rendered_event
    assert "Hợp đồng.pdf" not in rendered_event
    assert staged not in rendered_event


async def test_confirm_without_a_staged_object_writes_nothing(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    with pytest.raises(DocumentUploadNotCompletedError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []
    assert repo.rows == {}
    assert audit.entries == []
    assert outbox.envelopes == []
    assert storage.copies == []
    assert storage.deleted == []


@pytest.mark.parametrize("content_type", ["text/html", "application/zip"])
async def test_confirm_rechecks_the_staged_content_type(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    content_type: str,
) -> None:
    _staged(storage, content_type=content_type)

    with pytest.raises(DocumentTypeNotAllowedError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []
    assert audit.entries == []
    assert outbox.envelopes == []
    assert storage.copies == []
    assert storage.deleted == []


async def test_confirm_rejects_a_staged_object_larger_than_the_claim(
    service: DocumentService, storage: FakeDocumentStorage, repo: FakeDocumentRepo
) -> None:
    _staged(storage, size_bytes=2048, claimed_size=1024)

    with pytest.raises(DocumentTooLargeError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []
    assert storage.copies == []


async def test_confirm_rejects_a_staged_object_over_the_cap(
    service: DocumentService, storage: FakeDocumentStorage, repo: FakeDocumentRepo
) -> None:
    _staged(
        storage,
        size_bytes=MAX_DOCUMENT_SIZE_BYTES + 1,
        claimed_size=MAX_DOCUMENT_SIZE_BYTES + 1,
    )

    with pytest.raises(DocumentTooLargeError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []
    assert storage.copies == []


async def test_confirm_rejects_an_empty_staged_object(
    service: DocumentService, storage: FakeDocumentStorage, repo: FakeDocumentRepo
) -> None:
    _staged(storage, size_bytes=0, claimed_size=1024)

    with pytest.raises(DocumentUploadNotCompletedError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"claimed-size": "1024"}, id="filename-missing"),
        pytest.param(
            {"filename": "%FF", "claimed-size": "1024"}, id="filename-not-utf8"
        ),
        pytest.param({"filename": "%00", "claimed-size": "1024"}, id="filename-nul"),
        pytest.param(
            {"filename": "a" * 256, "claimed-size": "1024"},
            id="filename-too-long",
        ),
        pytest.param({"filename": "contract.pdf"}, id="claim-missing"),
        pytest.param(
            {"filename": "contract.pdf", "claimed-size": "abc"}, id="claim-not-int"
        ),
        pytest.param(
            {"filename": "contract.pdf", "claimed-size": "0"}, id="claim-empty"
        ),
    ],
)
async def test_confirm_requires_the_upload_protocol_metadata(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    metadata: dict[str, str],
) -> None:
    _staged(storage, metadata=metadata)

    with pytest.raises(DocumentUploadNotCompletedError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []
    assert storage.copies == []
    assert storage.deleted == []


async def test_confirm_replay_after_success_is_incomplete(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _staged(storage)

    await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    with pytest.raises(DocumentUploadNotCompletedError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert len(repo.rows) == 1
    assert len(audit.entries) == 1
    assert len(outbox.envelopes) == 1


async def test_confirm_copy_failure_writes_nothing(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    staged = _staged(storage)
    storage.fail_copy = True

    with pytest.raises(RuntimeError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert staged in storage.objects
    assert repo.writes == []
    assert audit.entries == []
    assert outbox.envelopes == []


async def test_confirm_copy_lost_to_a_concurrent_purge_is_409(
    service: DocumentService,
    storage: FakeDocumentStorage,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _staged(storage)
    storage.copy_error = ObjectMissingError()

    with pytest.raises(DocumentUploadNotCompletedError):
        await service.create(identity(Role.HR), SELF, _confirm(), NOW)

    assert repo.writes == []
    assert audit.entries == []
    assert outbox.envelopes == []
    assert storage.deleted == []


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_confirm_denied_for_non_management(
    service: DocumentService, storage: FakeDocumentStorage, role: Role
) -> None:
    _staged(storage)

    with pytest.raises(ForbiddenError):
        await service.create(identity(role), SELF, _confirm(), NOW)

    assert storage.copies == []
    assert storage.deleted == []


async def test_write_paths_require_an_employee_in_the_tenant(
    service: DocumentService,
    storage: FakeDocumentStorage,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    hr = identity(Role.HR)
    _staged(storage)

    with pytest.raises(EmployeeNotFoundError):
        await service.create(hr, UNKNOWN_EMPLOYEE, _confirm(), NOW)
    with pytest.raises(EmployeeNotFoundError):
        await service.update(
            hr, UNKNOWN_EMPLOYEE, DOCUMENT_ID, DocumentUpdate(title="x"), NOW
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.delete(hr, UNKNOWN_EMPLOYEE, DOCUMENT_ID, NOW)

    assert storage.copies == []
    assert storage.deleted == []
    assert audit.entries == []
    assert outbox.envelopes == []


# --- reads -------------------------------------------------------------------


async def test_manager_lists_self_and_direct_reports_only(
    service: DocumentService, repo: FakeDocumentRepo
) -> None:
    await _seed_document(repo, document_id=DOCUMENT_ID, employee_id=SELF)
    await _seed_document(repo, document_id=DOCUMENT_ID_2, employee_id=REPORT)
    manager = identity(Role.MANAGER, employee_id=SELF)

    own = await service.list(manager, SELF, page=PageParams())
    team = await service.list(manager, REPORT, page=PageParams())

    assert [row.id for row in own.items] == [DOCUMENT_ID]
    assert [row.id for row in team.items] == [DOCUMENT_ID_2]
    with pytest.raises(ForbiddenError):
        await service.list(manager, PEER, page=PageParams())


async def test_employee_reads_only_their_own_documents(
    service: DocumentService, repo: FakeDocumentRepo
) -> None:
    await _seed_document(repo, employee_id=PEER)
    employee = identity(Role.EMPLOYEE, employee_id=SELF)

    page = await service.list(employee, SELF, page=PageParams())
    assert page.items == []

    with pytest.raises(ForbiddenError):
        await service.list(employee, PEER, page=PageParams())
    with pytest.raises(ForbiddenError):
        await service.get(employee, PEER, DOCUMENT_ID)


async def test_finance_reads_only_their_own_documents(
    service: DocumentService, repo: FakeDocumentRepo
) -> None:
    await _seed_document(repo, employee_id=PEER)
    finance = identity(Role.FINANCE, employee_id=SELF)

    page = await service.list(finance, SELF, page=PageParams())
    assert page.items == []

    with pytest.raises(ForbiddenError):
        await service.get(finance, PEER, DOCUMENT_ID)


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
async def test_management_roles_read_any_employee(
    service: DocumentService, repo: FakeDocumentRepo, role: Role
) -> None:
    await _seed_document(repo, employee_id=PEER)

    page = await service.list(identity(role), PEER, page=PageParams())

    assert [row.id for row in page.items] == [DOCUMENT_ID]


@pytest.mark.parametrize("employee_id", [OTHER, UNKNOWN_EMPLOYEE])
async def test_reads_of_a_foreign_or_unknown_employee_are_404(
    service: DocumentService, employee_id: UUID
) -> None:
    with pytest.raises(EmployeeNotFoundError):
        await service.list(identity(Role.HR), employee_id, page=PageParams())


async def test_document_id_is_scoped_to_the_employee_path(
    service: DocumentService, repo: FakeDocumentRepo
) -> None:
    await _seed_document(repo, employee_id=PEER)

    with pytest.raises(DocumentNotFoundError):
        await service.get(identity(Role.HR), SELF, DOCUMENT_ID)


async def test_list_filters_by_category_and_hides_deleted(
    service: DocumentService, repo: FakeDocumentRepo
) -> None:
    await _seed_document(repo, document_id=DOCUMENT_ID)
    await _seed_document(
        repo,
        document_id=DOCUMENT_ID_2,
        category=DocumentCategory.CERTIFICATION,
    )

    page = await service.list(
        identity(Role.HR),
        SELF,
        category=DocumentCategory.CERTIFICATION,
        page=PageParams(),
    )
    assert [row.id for row in page.items] == [DOCUMENT_ID_2]
    assert page.page.total_items == 1

    await repo.soft_delete(TENANT_A, SELF, DOCUMENT_ID_2, now=NOW)
    page = await service.list(
        identity(Role.HR),
        SELF,
        category=DocumentCategory.CERTIFICATION,
        page=PageParams(),
    )
    assert page.items == []
    assert page.page.total_items == 0


async def test_get_and_download_of_a_deleted_document_are_404(
    service: DocumentService, repo: FakeDocumentRepo, storage: FakeDocumentStorage
) -> None:
    await _seed_document(repo)
    await repo.soft_delete(TENANT_A, SELF, DOCUMENT_ID, now=NOW)
    hr = identity(Role.HR)

    with pytest.raises(DocumentNotFoundError):
        await service.get(hr, SELF, DOCUMENT_ID)
    with pytest.raises(DocumentNotFoundError):
        await service.download(hr, SELF, DOCUMENT_ID)

    assert storage.downloads == []


async def test_download_mints_a_fresh_url_on_every_call(
    service: DocumentService, repo: FakeDocumentRepo, storage: FakeDocumentStorage
) -> None:
    document = await _seed_document(repo)
    owner = identity(Role.EMPLOYEE, employee_id=SELF)

    first = await service.download(owner, SELF, document.id)
    second = await service.download(owner, SELF, document.id)

    key = document_key(TENANT_A, SELF, document.id)
    assert first.url != second.url
    assert storage.downloads == [key, key]
    assert "contract.pdf" not in first.url


async def test_denied_download_mints_no_url(
    service: DocumentService, repo: FakeDocumentRepo, storage: FakeDocumentStorage
) -> None:
    await _seed_document(repo, employee_id=PEER)

    with pytest.raises(ForbiddenError):
        await service.download(
            identity(Role.EMPLOYEE, employee_id=SELF), PEER, DOCUMENT_ID
        )

    assert storage.downloads == []


# --- metadata update ---------------------------------------------------------


async def test_update_audits_title_without_its_value_and_emits_no_event(
    service: DocumentService,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    await _seed_document(repo, title="Old title")

    updated = await service.update(
        identity(Role.HR),
        SELF,
        DOCUMENT_ID,
        DocumentUpdate(title="New title"),
        NOW,
    )

    assert updated.title == "New title"
    [entry] = audit.entries
    assert entry.action == "document.updated"
    assert entry.entity_type == "document"
    assert entry.entity_id == DOCUMENT_ID
    assert entry.changes == {"title": PII_CHANGED}
    rendered = repr(entry.changes)
    assert "New title" not in rendered
    assert "Old title" not in rendered
    assert outbox.envelopes == []


async def test_update_audits_category_and_expiry_old_to_new(
    service: DocumentService,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    await _seed_document(
        repo,
        category=DocumentCategory.CONTRACT,
        expiry_date=date(2026, 1, 1),
    )

    updated = await service.update(
        identity(Role.HR),
        SELF,
        DOCUMENT_ID,
        DocumentUpdate(
            category=DocumentCategory.CERTIFICATION,
            expiry_date=date(2027, 12, 31),
        ),
        NOW,
    )

    assert updated.category is DocumentCategory.CERTIFICATION
    [entry] = audit.entries
    changes = entry.changes
    assert changes is not None
    assert changes["category"] == {
        "old": "contract",
        "new": "certification",
    }
    assert changes["expiry_date"] == {
        "old": "2026-01-01",
        "new": "2027-12-31",
    }
    assert outbox.envelopes == []


async def test_update_can_clear_the_expiry_date(
    service: DocumentService,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
) -> None:
    await _seed_document(repo, expiry_date=date(2026, 1, 1))

    updated = await service.update(
        identity(Role.HR),
        SELF,
        DOCUMENT_ID,
        DocumentUpdate(expiry_date=None),
        NOW,
    )

    assert updated.expiry_date is None
    [entry] = audit.entries
    changes = entry.changes
    assert changes is not None
    assert changes["expiry_date"] == {
        "old": "2026-01-01",
        "new": None,
    }


async def test_update_missing_document_is_404(
    service: DocumentService, audit: RecordingAuditWriter
) -> None:
    with pytest.raises(DocumentNotFoundError):
        await service.update(
            identity(Role.HR),
            SELF,
            DOCUMENT_ID,
            DocumentUpdate(title="New title"),
            NOW,
        )
    assert audit.entries == []


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_update_denied_for_non_management(
    service: DocumentService,
    repo: FakeDocumentRepo,
    audit: RecordingAuditWriter,
    role: Role,
) -> None:
    await _seed_document(repo, title="Old title")

    with pytest.raises(ForbiddenError):
        await service.update(
            identity(role),
            SELF,
            DOCUMENT_ID,
            DocumentUpdate(title="New title"),
            NOW,
        )

    existing = await repo.get(TENANT_A, SELF, DOCUMENT_ID)
    assert existing is not None and existing.title == "Old title"
    assert audit.entries == []


# --- delete ------------------------------------------------------------------


async def test_delete_purges_the_object_before_soft_deleting(
    service: DocumentService,
    repo: FakeDocumentRepo,
    storage: FakeDocumentStorage,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    journal: list[str],
) -> None:
    document = await _seed_document(repo)
    key = document_key(TENANT_A, SELF, document.id)
    storage.objects[key] = object_stat()

    await service.delete(identity(Role.HR), SELF, document.id, NOW)

    assert storage.deleted == [key]
    assert key not in storage.objects
    assert (TENANT_A, SELF, document.id) in repo.deleted
    assert journal.index("storage.delete") < journal.index("repo.soft_delete")

    [entry] = audit.entries
    assert entry.action == "document.deleted"
    assert entry.entity_type == "document"
    assert entry.entity_id == document.id
    assert entry.changes == {"deleted_at": {"old": None, "new": NOW.isoformat()}}

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "document.deleted"
    assert envelope["data"] == {
        "document_id": str(document.id),
        "employee_id": str(SELF),
        "category": "contract",
    }
    assert kafka_message_key(envelope) == str(SELF)

    with pytest.raises(DocumentNotFoundError):
        await service.delete(identity(Role.HR), SELF, document.id, NOW)
    assert storage.deleted == [key]


async def test_delete_storage_failure_leaves_the_row_and_trail_untouched(
    service: DocumentService,
    repo: FakeDocumentRepo,
    storage: FakeDocumentStorage,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    document = await _seed_document(repo)
    storage.objects[document_key(TENANT_A, SELF, document.id)] = object_stat()
    storage.fail_delete = True

    with pytest.raises(RuntimeError):
        await service.delete(identity(Role.HR), SELF, document.id, NOW)

    assert await repo.get(TENANT_A, SELF, document.id) == document
    assert (TENANT_A, SELF, document.id) not in repo.deleted
    assert audit.entries == []
    assert outbox.envelopes == []


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_delete_denied_for_non_management(
    service: DocumentService,
    repo: FakeDocumentRepo,
    storage: FakeDocumentStorage,
    audit: RecordingAuditWriter,
    role: Role,
) -> None:
    document = await _seed_document(repo)
    storage.objects[document_key(TENANT_A, SELF, document.id)] = object_stat()

    with pytest.raises(ForbiddenError):
        await service.delete(identity(role), SELF, document.id, NOW)

    assert storage.deleted == []
    assert (TENANT_A, SELF, document.id) not in repo.deleted
    assert audit.entries == []


# --- tenant isolation --------------------------------------------------------


async def test_a_foreign_tenant_cannot_reach_a_document(
    service: DocumentService, repo: FakeDocumentRepo, storage: FakeDocumentStorage
) -> None:
    document = await _seed_document(repo)
    intruder = identity(Role.HR, tenant_id=TENANT_B, employee_id=OTHER)

    with pytest.raises(EmployeeNotFoundError):
        await service.get(intruder, SELF, document.id)
    with pytest.raises(EmployeeNotFoundError):
        await service.download(intruder, SELF, document.id)
    with pytest.raises(EmployeeNotFoundError):
        await service.update(
            intruder, SELF, document.id, DocumentUpdate(title="Stolen"), NOW
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.delete(intruder, SELF, document.id, NOW)

    assert await repo.get(TENANT_A, SELF, document.id) == document
    assert storage.downloads == []
    assert storage.deleted == []


async def test_created_row_and_event_are_keyed_to_the_acting_tenant(
    service: DocumentService,
    repo: FakeDocumentRepo,
    storage: FakeDocumentStorage,
    outbox: RecordingEventOutbox,
) -> None:
    _staged(storage, tenant_id=TENANT_B)
    document = await service.create(
        identity(Role.HR, tenant_id=TENANT_B, employee_id=OTHER),
        OTHER,
        _confirm(),
        NOW,
    )

    assert await repo.get(TENANT_B, OTHER, document.id) == document
    assert await repo.get(TENANT_A, OTHER, document.id) is None
    [envelope] = outbox.envelopes
    assert envelope["tenant_id"] == str(TENANT_B)
    assert envelope["data"]["employee_id"] == str(OTHER)
