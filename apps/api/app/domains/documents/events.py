"""Event payload builders for the Documents domain (T4).

The two v1 contracts live under `contracts/events/documents/` and landed in T1,
before any producer (AGENTS.md rule 4). These are pure functions returning full
envelope-shaped dicts; `DocumentService` hands exactly this output to the
`EventOutbox` port in the same transaction as the upload/delete state change
(ADR 0012).

Payloads carry identifiers and the category code only. `title`, `filename`,
storage keys and presigned URLs are PII (`.ai/security.md`) and never appear
here — the contract test sweeps for those keys. Every payload carries
`employee_id` (the subject), so the ADR 0012 Kafka key resolves through its
first fallback and needs no amendment.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.documents.schemas import DocumentCategory

EventName = Literal["document.uploaded", "document.deleted"]


def _envelope(
    event_type: EventName,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a validated domain payload in the shared v1 envelope.

    `occurred_at` is the domain time of the fact (RFC 3339 date-time in UTC);
    the router-injected `now` clock (conventions) flows into it so builders
    stay deterministic — the outbox stores and the relay publishes it verbatim
    (ADR 0012), never re-stamped.
    """
    return {
        "event_id": f"evt_{uuid7()}",
        "event_type": event_type,
        "version": 1,
        "tenant_id": str(tenant_id),
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "actor_id": str(actor_id),
        "data": data,
    }


def document_uploaded(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    document_id: UUID,
    employee_id: UUID,
    category: DocumentCategory,
) -> dict[str, Any]:
    """Envelope for `document.uploaded` — the row was created after confirm.

    `employee_id` is the document's subject and the Kafka message key; the
    title, filename, storage key and presigned URL never leave the database.
    """
    return _envelope(
        "document.uploaded",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "document_id": str(document_id),
            "employee_id": str(employee_id),
            "category": category.value,
        },
    )


def document_deleted(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    document_id: UUID,
    employee_id: UUID,
    category: DocumentCategory,
) -> dict[str, Any]:
    """Envelope for `document.deleted` — soft-deleted and object purged.

    Identifiers and category only; the former title, filename, storage key and
    presigned URLs are never emitted.
    """
    return _envelope(
        "document.deleted",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "document_id": str(document_id),
            "employee_id": str(employee_id),
            "category": category.value,
        },
    )
