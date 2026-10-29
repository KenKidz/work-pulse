"""`DocumentStorage` port — private object storage for employee documents (T2).

The domain owns this protocol; the MinIO/S3 adapter lives in
`app.infrastructure.storage` (architecture rule 3 — no storage client import
here). Object keys never contain filenames (`.ai/security.md`, ADR 0022):

- staging: `staging/{tenant_id}/{upload_id}`
- final: `documents/{tenant_id}/{employee_id}/{document_id}`

Presigned URLs are bearer credentials. They are minted for one authorized
caller, never logged, never cached and never emitted. The upload cap is 10
minutes and the download cap 5 minutes; adapters clamp a configured or
caller-supplied TTL to those maxima so no other code path can mint a longer
lived URL. The default TTLs (600 s / 300 s) come from `Settings` and are
passed in by the adapter factory.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

#: Presigned upload URLs never outlive 10 minutes (feature spec).
MAX_UPLOAD_TTL = timedelta(minutes=10)
#: Presigned download URLs never outlive 5 minutes (feature spec).
MAX_DOWNLOAD_TTL = timedelta(minutes=5)


def staging_key(tenant_id: UUID, upload_id: UUID) -> str:
    """The exact key an upload intent is pinned to."""
    return f"staging/{tenant_id}/{upload_id}"


def document_key(tenant_id: UUID, employee_id: UUID, document_id: UUID) -> str:
    """The final key of a confirmed document (never a filename)."""
    return f"documents/{tenant_id}/{employee_id}/{document_id}"


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """A presigned POST target plus the form fields the client must send."""

    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PresignedDownload:
    """A fresh presigned GET URL and its expiry."""

    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """The HEAD result confirm re-checks (belt-and-braces, ADR 0022).

    `metadata` is user metadata only (`x-amz-meta-*`), keys lowercased and
    prefix-stripped — the adapter normalizes whatever the store returns.
    """

    size_bytes: int
    content_type: str
    metadata: Mapping[str, str]


class ObjectMissingError(Exception):
    """The source object vanished before a copy.

    Raised by `DocumentStorage.copy` when a concurrent confirm won the staging
    purge race after this call's HEAD. The service maps it to the pinned
    `409 document_upload_not_completed`; a raw storage-client error must never
    surface as a 500 (ADR 0022).
    """


class DocumentStorage(Protocol):
    """Private object storage for document bytes.

    Every method is async. Adapters offload their blocking client calls to a
    worker thread; the domain never sees an S3/MinIO type.
    """

    async def create_upload(
        self,
        tenant_id: UUID,
        upload_id: UUID,
        *,
        content_type: str,
        size_bytes: int,
        ttl: timedelta | None = None,
    ) -> PresignedUpload:
        """Presign a POST pinned to the staging key and the claim.

        The policy fixes the exact key, the claimed content type and size, the
        `x-amz-meta-filename` channel and a `content-length-range` of
        `1..size_bytes`. A `None` TTL uses the adapter's configured default;
        any value is clamped to `MAX_UPLOAD_TTL`.
        """
        ...

    async def stat(self, key: str) -> ObjectStat | None:
        """HEAD one object; `None` when it does not exist."""
        ...

    async def copy(self, source_key: str, destination_key: str) -> None:
        """Server-side copy (confirm moves staging → final, ADR 0022).

        Raises `ObjectMissingError` when the source no longer exists — a
        concurrent confirm purged staging after this call's HEAD. The service
        maps that to the pinned `409`, never a raw client error.
        """
        ...

    async def delete(self, key: str) -> None:
        """Delete one object; a missing key is a no-op (delete is idempotent)."""
        ...

    async def presign_download(
        self, key: str, ttl: timedelta | None = None
    ) -> PresignedDownload:
        """Mint a **fresh** presigned GET; any TTL is clamped to
        `MAX_DOWNLOAD_TTL`."""
        ...
