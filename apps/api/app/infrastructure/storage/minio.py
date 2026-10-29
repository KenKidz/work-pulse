"""MinIO/S3 adapter for the `DocumentStorage` port (T2, ADR 0022).

boto3 is synchronous; every call crosses `anyio.to_thread.run_sync` so the API
event loop is never blocked (the notification senders' precedent). The bucket
is private: the only access paths are the presigned POST/GET URLs minted here.

The presigned POST policy is the security boundary (ADR 0022 decision 2):

- `eq $key` — the exact `staging/{tenant_id}/{upload_id}` key;
- `eq $Content-Type` — the claim checked by the service at intent time;
- `eq $x-amz-meta-claimed-size` — the claim re-checked at confirm time;
- `starts-with $x-amz-meta-filename ""` — the filename channel (percent-encoded
  UTF-8, at most 255 decoded characters);
- `content-length-range 1 <size_bytes>` — a body larger than the claim is
  refused by storage, belt-and-braces with the confirm re-check.

Configured TTLs are clamped to the domain maxima (`MAX_UPLOAD_TTL` /
`MAX_DOWNLOAD_TTL`) so no configuration can mint a longer-lived URL than the
feature spec allows. Keys, filenames and URLs are never logged here.
"""

from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Protocol, cast
from uuid import UUID

import anyio
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings, get_settings
from app.domains.documents.storage import (
    MAX_DOWNLOAD_TTL,
    MAX_UPLOAD_TTL,
    DocumentStorage,
    ObjectMissingError,
    ObjectStat,
    PresignedDownload,
    PresignedUpload,
    staging_key,
)

#: MinIO ignores the region, but SigV4 requires a value (the S3 default).
_REGION = "us-east-1"

#: `head_object` returns one of these shapes for a missing object.
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class S3Client(Protocol):
    """The subset of the boto3 S3 client this adapter uses (injectable)."""

    def generate_presigned_post(
        self,
        *,
        Bucket: str,
        Key: str,
        Fields: dict[str, str],
        Conditions: list[Any],
        ExpiresIn: int,
    ) -> dict[str, Any]:
        """Return `{"url": ..., "fields": {...}}` for a browser POST."""
        ...

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        """Return a presigned URL for one S3 operation."""
        ...

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Return object metadata or raise `ClientError`."""
        ...

    def copy_object(
        self, *, Bucket: str, Key: str, CopySource: dict[str, str]
    ) -> dict[str, Any]:
        """Server-side copy into `Key`."""
        ...

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Delete one object (idempotent on S3-compatible stores)."""
        ...


class MinioDocumentStorage(DocumentStorage):
    """`DocumentStorage` over a private MinIO/S3 bucket."""

    def __init__(
        self,
        *,
        client: S3Client,
        bucket: str,
        upload_ttl_seconds: int,
        download_ttl_seconds: int,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._upload_ttl_seconds = upload_ttl_seconds
        self._download_ttl_seconds = download_ttl_seconds

    async def create_upload(
        self,
        tenant_id: UUID,
        upload_id: UUID,
        *,
        content_type: str,
        size_bytes: int,
        ttl: timedelta | None = None,
    ) -> PresignedUpload:
        seconds = _ttl_seconds(ttl, self._upload_ttl_seconds, MAX_UPLOAD_TTL)
        key = staging_key(tenant_id, upload_id)
        claimed = str(size_bytes)
        fields = {
            "Content-Type": content_type,
            "x-amz-meta-claimed-size": claimed,
        }
        conditions: list[Any] = [
            {"key": key},
            {"Content-Type": content_type},
            {"x-amz-meta-claimed-size": claimed},
            ["starts-with", "$x-amz-meta-filename", ""],
            ["content-length-range", 1, size_bytes],
        ]
        expires_at = datetime.now(UTC) + timedelta(seconds=seconds)
        response = await anyio.to_thread.run_sync(
            partial(
                self._client.generate_presigned_post,
                Bucket=self._bucket,
                Key=key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=seconds,
            )
        )
        return PresignedUpload(
            url=str(response["url"]),
            fields=dict(response["fields"]),
            expires_at=expires_at,
        )

    async def stat(self, key: str) -> ObjectStat | None:
        try:
            head = await anyio.to_thread.run_sync(
                partial(self._client.head_object, Bucket=self._bucket, Key=key)
            )
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise
        metadata = {
            str(name).lower(): str(value)
            for name, value in (head.get("Metadata") or {}).items()
        }
        return ObjectStat(
            size_bytes=int(head.get("ContentLength", 0)),
            content_type=str(head.get("ContentType", "")),
            metadata=metadata,
        )

    async def copy(self, source_key: str, destination_key: str) -> None:
        try:
            await anyio.to_thread.run_sync(
                partial(
                    self._client.copy_object,
                    Bucket=self._bucket,
                    Key=destination_key,
                    CopySource={"Bucket": self._bucket, "Key": source_key},
                )
            )
        except ClientError as exc:
            if _is_missing(exc):
                # A concurrent confirm purged staging after this call's HEAD.
                # The service maps this to the pinned 409, never a 500.
                raise ObjectMissingError() from None
            raise

    async def delete(self, key: str) -> None:
        await anyio.to_thread.run_sync(
            partial(self._client.delete_object, Bucket=self._bucket, Key=key)
        )

    async def presign_download(
        self, key: str, ttl: timedelta | None = None
    ) -> PresignedDownload:
        seconds = _ttl_seconds(ttl, self._download_ttl_seconds, MAX_DOWNLOAD_TTL)
        expires_at = datetime.now(UTC) + timedelta(seconds=seconds)
        url = await anyio.to_thread.run_sync(
            partial(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=seconds,
            )
        )
        return PresignedDownload(url=str(url), expires_at=expires_at)


def create_document_storage(settings: Settings | None = None) -> MinioDocumentStorage:
    """Build the adapter from settings (a boto3 client is created lazily)."""
    resolved = settings or get_settings()
    client = cast(
        S3Client,
        boto3.client(
            "s3",
            endpoint_url=resolved.storage_endpoint,
            aws_access_key_id=resolved.storage_access_key,
            aws_secret_access_key=resolved.storage_secret_key,
            region_name=_REGION,
            use_ssl=resolved.storage_endpoint.lower().startswith("https://"),
            # MinIO is addressed path-style (localhost:9000/bucket/key); the
            # same client works against a real S3 deployment later (ADR 0022).
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        ),
    )
    return MinioDocumentStorage(
        client=client,
        bucket=resolved.storage_bucket,
        upload_ttl_seconds=resolved.storage_upload_ttl_seconds,
        download_ttl_seconds=resolved.storage_download_ttl_seconds,
    )


_storage: MinioDocumentStorage | None = None


def get_document_storage() -> MinioDocumentStorage:
    """Return the process-wide adapter, creating it on first use."""
    global _storage
    if _storage is None:
        _storage = create_document_storage()
    return _storage


def _ttl_seconds(
    ttl: timedelta | None, default_seconds: int, maximum: timedelta
) -> int:
    """Resolve a TTL to whole seconds, clamped to `1..maximum`."""
    requested = default_seconds if ttl is None else int(ttl.total_seconds())
    return max(1, min(requested, int(maximum.total_seconds())))


def _is_missing(exc: ClientError) -> bool:
    """Whether a `ClientError` is the store's missing-object answer.

    Deliberately code-based: a missing *bucket* (`NoSuchBucket`, also a 404)
    is a misconfiguration and must surface, not masquerade as an incomplete
    upload.
    """
    error = exc.response.get("Error") or {}
    return str(error.get("Code", "")) in _NOT_FOUND_CODES


__all__ = [
    "MinioDocumentStorage",
    "S3Client",
    "create_document_storage",
    "get_document_storage",
]
