"""Strict boot configuration check for deployed processes (deployment T2).

Every `Settings` field has a dev-localhost default, which is exactly right for
`make dev`, tests and compose but silently wrong in a cluster. Images and
manifests set `WORKPULSE_STRICT_CONFIG=1`; this module then requires the
connection-critical keys to be present in the process environment and exits 1
with a plain-text `missing configuration: <KEY>` line naming the first absent
key.

The check never prints a value, never prints a traceback and runs before any
connection attempt (`.ai/security.md`). Local development leaves the flag
unset, so the existing default-driven flows are untouched (business rule 8).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

#: Keys a deployed process must receive from the environment. Mirrored by
#: `.env.example` and the Kubernetes Secret placeholder key list.
REQUIRED_BOOT_KEYS: tuple[str, ...] = (
    "DATABASE_URL",
    "REDIS_URL",
    "KAFKA_BOOTSTRAP_SERVERS",
    "CLICKHOUSE_URL",
    "STORAGE_ENDPOINT",
    "STORAGE_ACCESS_KEY",
    "STORAGE_SECRET_KEY",
    "STORAGE_BUCKET",
)

#: Opt-in flag set by the images/manifests, never by local dev or tests.
STRICT_CONFIG_FLAG = "WORKPULSE_STRICT_CONFIG"


def validate_boot_configuration(environ: Mapping[str, str] | None = None) -> None:
    """Exit 1 when strict mode is on and a required key is missing/blank.

    `environ` is injectable for tests; production callers use the process
    environment. A blank value is treated as missing: an empty Secret key is
    as wrong as an absent one, and the message names the key only.
    """
    env = os.environ if environ is None else environ
    if env.get(STRICT_CONFIG_FLAG) != "1":
        return

    for key in REQUIRED_BOOT_KEYS:
        if not env.get(key, "").strip():
            print(f"missing configuration: {key}", file=sys.stderr)
            sys.exit(1)
