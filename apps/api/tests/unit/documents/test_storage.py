"""Unit tests for the `DocumentStorage` port and MinIO adapter (T2).

No network: a fake S3 client records every presign/HEAD/copy/delete call and
raises the same `ClientError` shape boto3 produces. The presign policy and the
TTL caps are the security surface under test (ADR 0022) — a longer-lived URL or
a policy that lets the browser pick another key/content-type/size is a
regression, not a style choice.

The `MinioDocumentStorage` conformance to the port is checked statically
(`S3Client`/`DocumentStorage` annotations); these tests prove the adapter's
behavior.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.domains.documents.storage import (
    MAX_DOWNLOAD_TTL,
    MAX_UPLOAD_TTL,
    ObjectMissingError,
    ObjectStat,
    document_key,
    staging_key,
)
from app.infrastructure.storage.minio import (
    MinioDocumentStorage,
    S3Client,
    create_document_storage,
)
from app.shared.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000002")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000003")
UPLOAD_ID = UUID("00000000-0000-4000-8000-000000000004")

BUCKET = "documents-test"


class FakeS3Client:
    """Records calls; returns canned presign/HEAD responses (no network)."""

    def __init__(
        self,
        *,
        head: dict[str, Any] | None = None,
        head_error: ClientError | None = None,
        copy_error: ClientError | None = None,
    ) -> None:
        self.post_calls: list[dict[str, Any]] = []
        self.url_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.copy_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self._head = head or {
            "ContentLength": 1024,
            "ContentType": "application/pdf",
            "Metadata": {"filename": "contract.pdf", "claimed-size": "1024"},
        }
        self._head_error = head_error
        self._copy_error = copy_error
        self._url_serial = 0

    def generate_presigned_post(
        self,
        *,
        Bucket: str,
        Key: str,
        Fields: dict[str, str],
        Conditions: list[Any],
        ExpiresIn: int,
    ) -> dict[str, Any]:
        self.post_calls.append(
            {
                "Bucket": Bucket,
                "Key": Key,
                "Fields": Fields,
                "Conditions": Conditions,
                "ExpiresIn": ExpiresIn,
            }
        )
        return {
            "url": f"https://minio.test/{Bucket}",
            "fields": {"key": Key, **Fields},
        }

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        self.url_calls.append(
            {
                "ClientMethod": ClientMethod,
                "Params": Params,
                "ExpiresIn": ExpiresIn,
            }
        )
        self._url_serial += 1
        return f"https://minio.test/{Params['Key']}?sig={self._url_serial}"

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.head_calls.append({"Bucket": Bucket, "Key": Key})
        if self._head_error is not None:
            raise self._head_error
        return self._head

    def copy_object(
        self, *, Bucket: str, Key: str, CopySource: dict[str, str]
    ) -> dict[str, Any]:
        self.copy_calls.append({"Bucket": Bucket, "Key": Key, "CopySource": CopySource})
        if self._copy_error is not None:
            raise self._copy_error
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.delete_calls.append({"Bucket": Bucket, "Key": Key})
        return {}


def _storage(
    client: S3Client,
    *,
    upload_ttl_seconds: int = 600,
    download_ttl_seconds: int = 300,
    breaker: CircuitBreaker | None = None,
) -> MinioDocumentStorage:
    return MinioDocumentStorage(
        client=client,
        bucket=BUCKET,
        upload_ttl_seconds=upload_ttl_seconds,
        download_ttl_seconds=download_ttl_seconds,
        breaker=breaker,
    )


class FakeClock:
    """Monotonic clock driven explicitly by tests — no wall-clock sleeps."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(
    clock: FakeClock, *, threshold: int = 3, cooldown: float = 30.0
) -> CircuitBreaker:
    return CircuitBreaker(
        "minio",
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        clock=clock,
    )


def _client_error(code: str, *, status: int | None = None) -> ClientError:
    response: dict[str, Any] = {"Error": {"Code": code, "Message": "test"}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status}
    return ClientError(response, "HeadObject")


async def test_create_upload_pins_the_staging_policy() -> None:
    client = FakeS3Client()
    storage = _storage(client)
    before = datetime.now(UTC)

    upload = await storage.create_upload(
        TENANT_ID,
        UPLOAD_ID,
        content_type="application/pdf",
        size_bytes=1024,
        ttl=MAX_UPLOAD_TTL,
    )

    key = f"staging/{TENANT_ID}/{UPLOAD_ID}"
    assert client.post_calls == [
        {
            "Bucket": BUCKET,
            "Key": key,
            "Fields": {
                "Content-Type": "application/pdf",
                "x-amz-meta-claimed-size": "1024",
            },
            "Conditions": [
                {"key": key},
                {"Content-Type": "application/pdf"},
                {"x-amz-meta-claimed-size": "1024"},
                ["starts-with", "$x-amz-meta-filename", ""],
                ["content-length-range", 1, 1024],
            ],
            "ExpiresIn": int(MAX_UPLOAD_TTL.total_seconds()),
        }
    ]
    assert upload.url == f"https://minio.test/{BUCKET}"
    assert upload.fields == {
        "key": key,
        "Content-Type": "application/pdf",
        "x-amz-meta-claimed-size": "1024",
    }
    assert upload.expires_at >= before + MAX_UPLOAD_TTL
    assert upload.expires_at <= datetime.now(UTC) + MAX_UPLOAD_TTL


async def test_create_upload_uses_the_configured_default_ttl() -> None:
    client = FakeS3Client()
    storage = _storage(client, upload_ttl_seconds=120)

    await storage.create_upload(
        TENANT_ID, UPLOAD_ID, content_type="image/png", size_bytes=10
    )

    assert client.post_calls[0]["ExpiresIn"] == 120


async def test_create_upload_clamps_an_overlong_ttl() -> None:
    client = FakeS3Client()
    storage = _storage(client)

    await storage.create_upload(
        TENANT_ID,
        UPLOAD_ID,
        content_type="image/jpeg",
        size_bytes=10,
        ttl=timedelta(days=1),
    )

    assert client.post_calls[0]["ExpiresIn"] == int(MAX_UPLOAD_TTL.total_seconds())


async def test_presign_download_is_fresh_and_clamped() -> None:
    client = FakeS3Client()
    storage = _storage(client, download_ttl_seconds=30)
    key = document_key(TENANT_ID, EMPLOYEE_ID, DOCUMENT_ID)

    first = await storage.presign_download(key)
    second = await storage.presign_download(key, ttl=timedelta(days=1))

    assert client.url_calls == [
        {
            "ClientMethod": "get_object",
            "Params": {"Bucket": BUCKET, "Key": key},
            "ExpiresIn": 30,
        },
        {
            "ClientMethod": "get_object",
            "Params": {"Bucket": BUCKET, "Key": key},
            "ExpiresIn": int(MAX_DOWNLOAD_TTL.total_seconds()),
        },
    ]
    # A fresh URL per call (the spec forbids server-side caching).
    assert first.url != second.url
    assert first.expires_at != second.expires_at


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
async def test_stat_returns_none_for_a_missing_object(code: str) -> None:
    client = FakeS3Client(head_error=_client_error(code))
    storage = _storage(client)
    key = f"staging/{TENANT_ID}/{UPLOAD_ID}"

    assert await storage.stat(key) is None
    assert client.head_calls == [{"Bucket": BUCKET, "Key": key}]


async def test_stat_propagates_other_storage_errors() -> None:
    client = FakeS3Client(head_error=_client_error("AccessDenied", status=403))
    storage = _storage(client)

    with pytest.raises(ClientError):
        await storage.stat("documents/k")


async def test_stat_does_not_hide_a_missing_bucket() -> None:
    # A missing bucket is a misconfiguration, not an abandoned upload: it must
    # surface instead of becoming `409 document_upload_not_completed`.
    client = FakeS3Client(head_error=_client_error("NoSuchBucket", status=404))
    storage = _storage(client)

    with pytest.raises(ClientError):
        await storage.stat("documents/k")


async def test_stat_maps_size_type_and_user_metadata() -> None:
    client = FakeS3Client(
        head={
            "ContentLength": 2048,
            "ContentType": "image/png",
            # Boto3 lowercases user metadata; the adapter normalizes anyway.
            "Metadata": {"Filename": "id.png", "Claimed-Size": "2048"},
        }
    )
    storage = _storage(client)

    assert await storage.stat("documents/k") == ObjectStat(
        size_bytes=2048,
        content_type="image/png",
        metadata={"filename": "id.png", "claimed-size": "2048"},
    )


async def test_copy_and_delete_use_the_private_bucket() -> None:
    client = FakeS3Client()
    storage = _storage(client)
    source = staging_key(TENANT_ID, UPLOAD_ID)
    destination = document_key(TENANT_ID, EMPLOYEE_ID, DOCUMENT_ID)

    await storage.copy(source, destination)
    await storage.delete(destination)

    assert client.copy_calls == [
        {
            "Bucket": BUCKET,
            "Key": destination,
            "CopySource": {"Bucket": BUCKET, "Key": source},
        }
    ]
    assert client.delete_calls == [{"Bucket": BUCKET, "Key": destination}]


async def test_copy_missing_source_raises_object_missing() -> None:
    """A lost staging-purge race is a port-level 409 signal, not a 500."""
    client = FakeS3Client(copy_error=_client_error("NoSuchKey", status=404))
    storage = _storage(client)

    with pytest.raises(ObjectMissingError):
        await storage.copy(
            staging_key(TENANT_ID, UPLOAD_ID),
            document_key(TENANT_ID, EMPLOYEE_ID, DOCUMENT_ID),
        )


async def test_copy_propagates_non_missing_errors() -> None:
    error = _client_error("InternalError", status=500)
    client = FakeS3Client(copy_error=error)
    storage = _storage(client)

    with pytest.raises(ClientError) as exc:
        await storage.copy(
            staging_key(TENANT_ID, UPLOAD_ID),
            document_key(TENANT_ID, EMPLOYEE_ID, DOCUMENT_ID),
        )
    assert exc.value is error


def test_keys_never_include_a_filename() -> None:
    assert staging_key(TENANT_ID, UPLOAD_ID) == f"staging/{TENANT_ID}/{UPLOAD_ID}"
    assert (
        document_key(TENANT_ID, EMPLOYEE_ID, DOCUMENT_ID)
        == f"documents/{TENANT_ID}/{EMPLOYEE_ID}/{DOCUMENT_ID}"
    )


def test_create_document_storage_sets_bounded_timeouts() -> None:
    """One hung MinIO call must be bounded before the breaker can help (T3)."""
    storage = create_document_storage(
        Settings(
            storage_connect_timeout_seconds=2.5,
            storage_read_timeout_seconds=12.0,
        )
    )
    config = cast(Any, storage._client).meta.config

    assert config.connect_timeout == 2.5
    assert config.read_timeout == 12.0
    # Timeouts are per attempt: pin one attempt so they bound the whole call.
    assert config.retries["total_max_attempts"] == 1


async def test_adapter_breaker_opens_after_the_threshold_and_fails_fast() -> None:
    """A failing store trips the breaker; the next call does no client I/O."""
    clock = FakeClock()
    client = FakeS3Client(
        head_error=_client_error("InternalError", status=500),
    )
    storage = _storage(client, breaker=_breaker(clock, threshold=3))

    for _ in range(3):
        with pytest.raises(ClientError):
            await storage.stat("staging/k")
    assert storage._breaker.state is BreakerState.OPEN
    assert len(client.head_calls) == 3

    with pytest.raises(CircuitOpenError):
        await storage.stat("staging/k")
    assert len(client.head_calls) == 3  # no outbound I/O while open


async def test_adapter_breaker_recovers_after_the_cooldown_trial() -> None:
    """A successful single half-open trial closes the breaker again."""
    clock = FakeClock()
    client = FakeS3Client(head_error=_client_error("InternalError", status=500))
    breaker = _breaker(clock, threshold=1)
    storage = _storage(client, breaker=breaker)

    with pytest.raises(ClientError):
        await storage.stat("staging/k")
    assert breaker.state is BreakerState.OPEN

    clock.advance(30.0)
    client._head_error = None  # the store recovered

    # Presigning is offline: it must fail fast while not closed, but it can
    # neither consume the half-open permit nor "prove" recovery.
    with pytest.raises(CircuitOpenError):
        await storage.create_upload(
            TENANT_ID, UPLOAD_ID, content_type="application/pdf", size_bytes=1
        )
    assert breaker.state is BreakerState.OPEN

    assert (await storage.stat("staging/k")) is not None
    assert breaker.state is BreakerState.CLOSED


async def test_missing_objects_do_not_trip_the_breaker() -> None:
    """An abandoned upload is a protocol answer, not a dependency failure."""
    clock = FakeClock()
    client = FakeS3Client(head_error=_client_error("NoSuchKey", status=404))
    breaker = _breaker(clock, threshold=1)
    storage = _storage(client, breaker=breaker)

    for _ in range(4):
        assert await storage.stat("staging/k") is None

    assert breaker.state is BreakerState.CLOSED
    assert len(client.head_calls) == 4


async def test_presign_success_does_not_reset_the_failure_streak() -> None:
    """Offline presigning is guarded but never a dependency-health signal."""
    clock = FakeClock()
    client = FakeS3Client(head_error=_client_error("InternalError", status=500))
    breaker = _breaker(clock, threshold=2)
    storage = _storage(client, breaker=breaker)

    with pytest.raises(ClientError):
        await storage.stat("staging/k")  # failure 1

    for _ in range(3):
        upload = await storage.create_upload(
            TENANT_ID, UPLOAD_ID, content_type="application/pdf", size_bytes=1
        )
        assert upload.url

    with pytest.raises(ClientError):
        await storage.stat("staging/k")  # failure 2: the presigns did not reset
    assert breaker.state is BreakerState.OPEN

    with pytest.raises(CircuitOpenError):
        await storage.create_upload(
            TENANT_ID, UPLOAD_ID, content_type="application/pdf", size_bytes=1
        )
    assert len(client.post_calls) == 3  # the guard did no I/O
