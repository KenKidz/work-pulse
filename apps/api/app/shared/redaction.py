"""Payload-free `last_error` text for persisted infrastructure errors.

Three layers persist error diagnostics — the outbox relay (`last_error` on the
outbox row), the notification service (`last_error` on a delivery) and the
provider adapters (which raise before the service stores) — and all three must
scrub the same way: a lower layer may echo request data back raw, JSON-escaped
or whitespace-collapsed, and any of it would otherwise leak payload contents or
PII into a column (`.ai/security.md`).

The algorithm lives here once; each layer supplies only the values it knows
about (envelope leaves, rendered message/recipient details, provider
credentials). That keeps accidental drift from reopening a redaction hole
while the needle sets stay intentionally layer-specific.
"""

import json
from collections.abc import Iterable

# `last_error` is diagnostics, not data: the result is whitespace-normalized
# and capped at this many characters before it is stored.
MAX_ERROR_CHARS = 300

# Input is bounded before the redaction scan, so an accidentally huge echoed
# message cannot cost unbounded CPU/memory; a value's JSON-escaped form can be
# several times longer than the value itself.
MAX_ERROR_INPUT_CHARS = MAX_ERROR_CHARS * 8

# Values shorter than this are never redacted: identifiers, dates and state
# words would otherwise mangle ordinary error text without protecting anything
# meaningful (`.ai/security.md`).
MIN_REDACT_CHARS = 3


def redaction_needles(values: Iterable[str | None]) -> tuple[str, ...]:
    """Every value and line that must never survive in error text.

    Each candidate is stripped, split into lines (so a provider echoing one
    line is still scrubbed) and deduplicated; values shorter than
    `MIN_REDACT_CHARS` are ignored. Longest first, so a whole body is redacted
    before its individual lines.
    """
    needles = {
        candidate.strip()
        for value in values
        if value is not None
        for candidate in (value, *value.splitlines())
        if len(candidate.strip()) >= MIN_REDACT_CHARS
    }
    return tuple(sorted(needles, key=len, reverse=True))


def safe_error_text(exc: BaseException, needles: Iterable[str]) -> str:
    """`Type: redacted message`, capped at `MAX_ERROR_CHARS` characters.

    The raw text is clipped before the redaction scan, every needle is replaced
    in raw and JSON-escaped form, whitespace is normalized, and the exception
    type is prefixed. An empty message keeps the type alone.
    """
    text = str(exc)[:MAX_ERROR_INPUT_CHARS]
    for needle in needles:
        if len(needle) < MIN_REDACT_CHARS:
            continue
        escaped = json.dumps(needle)[1:-1]
        text = text.replace(needle, "[redacted]")
        if escaped != needle:
            text = text.replace(escaped, "[redacted]")
    text = " ".join(text.split())

    prefix = type(exc).__name__
    if not text:
        return prefix[:MAX_ERROR_CHARS]
    result = f"{prefix}: {text}"
    if len(result) > MAX_ERROR_CHARS:
        result = result[: MAX_ERROR_CHARS - 1] + "…"
    return result
