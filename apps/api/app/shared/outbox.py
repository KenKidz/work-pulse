"""Transactional outbox port (`.ai/architecture.md` → "Outbox, not dual writes").

Domains hand their finished event envelopes to an `EventOutbox` port; the
Postgres writer stores them inside the request's session, so the business row,
its audit entry and its outbox row commit in one transaction. The relay worker
then publishes committed rows to Kafka at-least-once — the transport contract
(topic, keying, delivery semantics, row shape) is pinned in ADR 0012.

Envelopes are stored and published verbatim: `event_id` and `occurred_at` are
the domain builder's (carrying the injected clock), never re-stamped.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.shared.errors import WorkPulseError

# The Kafka message key is the first present field in this order (ADR 0012 and
# its 2026-10-21 / 2026-10-23 amendments). Employee facts keep per-employee
# ordering, run-level payroll facts order by run, recruitment candidate events
# order by candidate, job events by job and interview-only facts by interview.
_MESSAGE_KEY_FIELDS = (
    "employee_id",
    "run_id",
    "candidate_id",
    "job_id",
    "interview_id",
)


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


def kafka_message_key(envelope: Mapping[str, Any]) -> str:
    """Resolve the Kafka message key for a stored envelope (ADR 0012).

    The key is the first present field of `data.employee_id` → `data.run_id` →
    `data.candidate_id` → `data.job_id` → `data.interview_id` (ADR 0012 and its
    2026-10-21 / 2026-10-23 amendments). Employee-scoped events keep the
    per-employee ordering pin; run-level payroll events order by run;
    recruitment candidate events order by candidate (interviews included,
    because their payloads carry `candidate_id`), job events by job. When
    several fields are present the earlier one wins — `candidate.hired` is
    employee-keyed, not candidate-keyed.
    """
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("event envelope data must be an object")

    for field in _MESSAGE_KEY_FIELDS:
        value = data.get(field)
        if value is not None and value != "":
            return str(value)

    raise ValueError(
        "event envelope data must carry employee_id, run_id, candidate_id, "
        "job_id or interview_id (Kafka key)"
    )


class EventEnvelope(BaseModel):
    """The pinned v1 envelope (`contracts/events/common/event-envelope.v1.json`).

    The outbox port's write-time gate. Mirrors the schema's
    `additionalProperties: false`, and additionally requires one of the pinned
    message-key fields — `data.employee_id`, `data.run_id`,
    `data.candidate_id`, `data.job_id` or `data.interview_id` (ADR 0012 and its
    2026-10-21 / 2026-10-23 amendments).
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
    def _data_must_carry_a_message_key(cls, data: dict[str, Any]) -> dict[str, Any]:
        try:
            kafka_message_key({"data": data})
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return data


class EventOutbox(Protocol):
    """Write-only port — the `AuditWriter` pattern for domain events.

    Implementations must append inside the caller's transaction: the state
    change, its audit entry and its outbox row commit together, or not at all.
    There is no update, delete or read path (rows are append-only, ADR 0012).
    """

    async def append(self, envelope: dict[str, Any]) -> None: ...

    async def append_many(self, envelopes: Sequence[dict[str, Any]]) -> None:
        """Append a batch in one write and one flush (finalize fan-out).

        Every envelope passes the same write-time gate; either the whole batch
        is added or an invalid envelope fails the caller's transaction.
        """
        ...
