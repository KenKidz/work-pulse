"""Transactional outbox port (`.ai/architecture.md` → "Outbox, not dual writes").

Domains hand their finished event envelopes to an `EventOutbox` port; the
Postgres writer stores them inside the request's session, so the business row,
its audit entry and its outbox row commit in one transaction. The relay worker
then publishes committed rows to Kafka at-least-once — the transport contract
(topic, keying, delivery semantics, row shape) is pinned in ADR 0012.

Envelopes are stored and published verbatim: `event_id` and `occurred_at` are
the domain builder's (carrying the injected clock), never re-stamped.
"""

from typing import Any, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.shared.errors import WorkPulseError


class InvalidEnvelopeError(WorkPulseError):
    """A payload that is not a valid v1 event envelope.

    Raised at write time so a builder bug fails the request transaction loudly
    instead of storing a junk row. The message is deliberately generic —
    validation detail can contain payload values, and payload contents must
    never reach logs or error surfaces (`.ai/security.md`).
    """

    status_code = 500
    code = "invalid_event_envelope"

    def __init__(
        self, message: str = "Outbox payload is not a valid v1 event envelope"
    ) -> None:
        super().__init__(message)


class EventEnvelope(BaseModel):
    """The pinned v1 envelope (`contracts/events/common/event-envelope.v1.json`).

    The outbox port's write-time gate. Mirrors the schema's
    `additionalProperties: false`, and additionally requires
    `data.employee_id` — all 7 payloads carry it, and it is the Kafka message
    key (ADR 0012 → per-employee ordering).
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(pattern=r"^evt_.+$")
    event_type: str = Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    version: int = Field(ge=1)
    tenant_id: UUID
    actor_id: UUID
    occurred_at: AwareDatetime
    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def _data_must_carry_employee_id(cls, data: dict[str, Any]) -> dict[str, Any]:
        if "employee_id" not in data:
            raise ValueError("envelope data must carry employee_id (Kafka key)")
        return data


class EventOutbox(Protocol):
    """Write-only port — the `AuditWriter` pattern for domain events.

    Implementations must append inside the caller's transaction: the state
    change, its audit entry and its outbox row commit together, or not at all.
    There is no update, delete or read path (rows are append-only, ADR 0012).
    """

    async def append(self, envelope: dict[str, Any]) -> None: ...
