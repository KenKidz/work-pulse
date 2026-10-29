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
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from app.domains.documents.storage import (
    MAX_DOWNLOAD_TTL,
    MAX_UPLOAD_TTL,
    ObjectStat,
    document_key,
    staging_key,
)
from app.infrastructure.storage.minio import MinioDocumentStorage, S3Client

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
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.delete_calls.append({"Bucket": Bucket, "Key": Key})
        return {}


def _storage(
    client: S3Client,
    *,
    upload_ttl_seconds: int = 600,
    download_ttl_seconds: int = 300,
) -> MinioDocumentStorage:
    return MinioDocumentStorage(
        client=client,
        bucket=BUCKET,
        upload_ttl_seconds=upload_ttl_seconds,
        download_ttl_seconds=download_ttl_seconds,
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


def test_keys_never_include_a_filename() -> None:
    assert staging_key(TENANT_ID, UPLOAD_ID) == f"staging/{TENANT_ID}/{UPLOAD_ID}"
    assert (
        document_key(TENANT_ID, EMPLOYEE_ID, DOCUMENT_ID)
        == f"documents/{TENANT_ID}/{EMPLOYEE_ID}/{DOCUMENT_ID}"
    )
