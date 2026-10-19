"""In-memory fakes for the outbox port (no I/O).

`RecordingEventOutbox` mirrors the `EventOutbox` port shape — append-only, no
read path — and records every envelope in order, so unit tests can assert what
a service handed the transactional outbox without a database or a bus
(`.ai/testing.md`: these tests pass with every container stopped).
"""

from typing import Any


class RecordingEventOutbox:
    """Records appended envelopes in order; optional raise hook.

    The hook lets a test simulate an adapter failure at the append call: the
    envelope is recorded first, then the configured error propagates to the
    caller.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self.envelopes: list[dict[str, Any]] = []
        self.error = error

    async def append(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)
        if self.error is not None:
            raise self.error
