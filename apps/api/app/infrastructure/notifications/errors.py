"""Payload-free provider error text for notification adapters (ADR 0015).

Provider SDKs may echo the request (rendered subject/body), the recipient's
account address or the credential in an exception message. `last_error` and
logs must never carry any of that (`.ai/security.md`), so adapters convert
provider failures to `NotificationProviderError` with every sensitive value —
raw and JSON-escaped, whole value and individual lines — scrubbed before the
text leaves the adapter. The service redacts again as defence in depth.

The scrub algorithm is shared (`app.shared.redaction`, ADR 0012's row rule):
clip the raw input, redact, normalize whitespace, then cap the stored text.
"""

from collections.abc import Iterable

from app.domains.notifications.exceptions import NotificationProviderError
from app.shared.redaction import redaction_needles, safe_error_text


def provider_error(
    exc: BaseException, *, sensitive: Iterable[str | None]
) -> NotificationProviderError:
    """Translate a provider exception into a payload- and PII-free error.

    `sensitive` is every value the adapter supplied to the SDK (credentials,
    message content, recipient details). The shared scrubber removes each value
    and its lines, raw or JSON-escaped, and caps the result.
    """
    return NotificationProviderError(safe_error_text(exc, redaction_needles(sensitive)))
