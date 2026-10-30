"""Documents × MinIO circuit breaker over HTTP (T3, ADR 0025).

The MinIO adapter wraps every client call in the `minio` breaker. An open
breaker short-circuits before any storage I/O and maps to the pinned
`503 dependency_unavailable`; the upload-intent/confirm paths fail before any
row, audit entry or outbox event exists. After the cooldown, exactly one
half-open trial calls the store; success closes the breaker and the surface
returns 2xx again.

The store is an in-memory stub here: these tests are about fail-fast semantics,
not storage behavior (the real bucket is exercised by the other documents
suites).
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_document_service
from app.domains.documents.models import Document as DocumentRow
from app.domains.documents.service import DocumentService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.employee_link import PostgresEmployeeLink
from app.infrastructure.postgres.manager_scope import PostgresEmployeeScope
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.repositories.documents import (
    PostgresDocumentRepository,
)
from app.infrastructure.storage.minio import MinioDocumentStorage
from app.main import app
from app.shared.circuit_breaker import BreakerState, CircuitBreaker
from app.shared.identity import Role
from tests.integration.conftest import ACTOR, ClientFactory, create_org_refs, identity
from tests.integration.documents_helpers import seed_employee

BUCKET = "documents-test"


class FakeClock:
    """Monotonic clock driven explicitly by tests — no wall-clock sleeps."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FlakyS3Client:
    """In-memory S3 stub that fails until `fail` is cleared; counts calls."""

    def __init__(self) -> None:
        self.fail = True
        self.post_calls = 0
        self.head_calls = 0
        self.copy_calls = 0
        self.delete_calls = 0

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("dependency down")

    def generate_presigned_post(
        self,
        *,
        Bucket: str,
        Key: str,
        Fields: dict[str, str],
        Conditions: list[Any],
        ExpiresIn: int,
    ) -> dict[str, Any]:
        self.post_calls += 1
        self._maybe_fail()
        return {
            "url": f"https://minio.test/{Bucket}",
            "fields": {"key": Key, **Fields},
        }

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        self._maybe_fail()
        return f"https://minio.test/{Params['Key']}"

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.head_calls += 1
        self._maybe_fail()
        return {
            "ContentLength": 1024,
            "ContentType": "application/pdf",
            "Metadata": {"filename": "signed-contract.pdf", "claimed-size": "1024"},
        }

    def copy_object(
        self, *, Bucket: str, Key: str, CopySource: dict[str, str]
    ) -> dict[str, Any]:
        self.copy_calls += 1
        self._maybe_fail()
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.delete_calls += 1
        self._maybe_fail()
        return {}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def s3_client() -> FlakyS3Client:
    return FlakyS3Client()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(
        "minio",
        failure_threshold=5,
        cooldown_seconds=30.0,
        clock=clock,
    )


@pytest.fixture
def document_service(
    session: AsyncSession,
    s3_client: FlakyS3Client,
    breaker: CircuitBreaker,
) -> Iterator[None]:
    """Wire the real service/repository to a stubbed MinIO adapter + breaker."""
    service = DocumentService(
        repo=PostgresDocumentRepository(session),
        storage=MinioDocumentStorage(
            client=s3_client,
            bucket=BUCKET,
            upload_ttl_seconds=600,
            download_ttl_seconds=300,
            breaker=breaker,
        ),
        employees=PostgresEmployeeLink(session),
        scope=PostgresEmployeeScope(session),
        audit=PostgresAuditWriter(session),
        outbox=PostgresEventOutbox(session, clock=lambda: datetime.now(UTC)),
    )
    app.dependency_overrides[get_document_service] = lambda: service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_document_service, None)


def _hr() -> Any:
    return identity(role=Role.HR, employee_id=None)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


def _trip(breaker: CircuitBreaker, *, failures: int = 5) -> None:
    """Open the breaker without touching the stub store."""

    def fail() -> None:
        raise RuntimeError("dependency down")

    for _ in range(failures):
        with pytest.raises(RuntimeError):
            breaker.call_sync(fail)
    assert breaker.state is BreakerState.OPEN


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


async def test_open_breaker_answers_503_without_storage_io(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_service: None,
    s3_client: FlakyS3Client,
    breaker: CircuitBreaker,
) -> None:
    """AC — an open breaker fails every storage path fast, writing nothing.

    The adapter unit tests prove client failures reach the threshold; this suite
    wires the real route and asserts the pinned 503, zero outbound I/O and zero
    orphan rows/audit/outbox.
    """
    await _seed_actor(client_factory, session)
    _trip(breaker)

    async with client_factory(_hr()) as hr:
        intent = await hr.post(
            f"/api/v1/employees/{ACTOR}/documents/uploads",
            json={"content_type": "application/pdf", "size_bytes": 1024},
        )
        confirm = await hr.post(
            f"/api/v1/employees/{ACTOR}/documents",
            json={
                "upload_id": str(uuid4()),
                "title": "Signed contract",
                "category": "contract",
            },
        )

    _assert_error(intent, 503, "dependency_unavailable")
    _assert_error(confirm, 503, "dependency_unavailable")
    assert s3_client.post_calls == 0  # fail fast: no outbound I/O
    await _assert_nothing_written(session)


async def test_breaker_recovers_after_the_cooldown_trial(
    client_factory: ClientFactory,
    session: AsyncSession,
    document_service: None,
    s3_client: FlakyS3Client,
    breaker: CircuitBreaker,
    clock: FakeClock,
) -> None:
    """AC — cooldown admits one real-I/O trial; success closes the breaker.

    The trial is a confirm (HEAD → copy → delete), not an offline presign: only
    a dependency-exercising call may prove recovery and close the breaker.
    """
    await _seed_actor(client_factory, session)
    _trip(breaker)

    async with client_factory(_hr()) as hr:
        blocked = await hr.post(
            f"/api/v1/employees/{ACTOR}/documents/uploads",
            json={"content_type": "application/pdf", "size_bytes": 1024},
        )
        assert blocked.status_code == 503
        assert s3_client.post_calls == 0  # guarded presign did no signing call

        clock.advance(30.0)
        s3_client.fail = False  # the dependency recovered
        trial = await hr.post(
            f"/api/v1/employees/{ACTOR}/documents",
            json={
                "upload_id": str(uuid4()),
                "title": "Signed contract",
                "category": "contract",
            },
        )
        assert trial.status_code == 201, trial.text
        assert breaker.state is BreakerState.CLOSED
        assert (s3_client.head_calls, s3_client.copy_calls, s3_client.delete_calls) == (
            1,
            1,
            1,
        )

        follow_up = await hr.post(
            f"/api/v1/employees/{ACTOR}/documents/uploads",
            json={"content_type": "application/pdf", "size_bytes": 1024},
        )

    assert follow_up.status_code == 201, follow_up.text
    assert s3_client.post_calls == 1
    assert breaker.state is BreakerState.CLOSED

    # The successful trial committed the document row, audit entry and event.
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


def test_storage_dependency_is_the_minio_breaker() -> None:
    """The adapter always has a `minio`-labelled breaker (bounded metric label)."""
    storage = MinioDocumentStorage(
        client=FlakyS3Client(),
        bucket=BUCKET,
        upload_ttl_seconds=600,
        download_ttl_seconds=300,
    )

    assert storage._breaker.dependency == "minio"
