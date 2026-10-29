"""Object-storage infrastructure: the private document bucket (ADR 0022).

Wired at the edges (FastAPI dependencies). Domains never import this package
directly — they depend on the `DocumentStorage` port in
`app.domains.documents.storage`.
"""

from app.infrastructure.storage.minio import (
    MinioDocumentStorage,
    S3Client,
    create_document_storage,
    get_document_storage,
)

__all__ = [
    "MinioDocumentStorage",
    "S3Client",
    "create_document_storage",
    "get_document_storage",
]
