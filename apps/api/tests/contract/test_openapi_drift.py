"""Contract drift gate — the checked-in OpenAPI file must equal the live app.

`make gen-api` regenerates `contracts/openapi/openapi.json`; if a router or
schema changes without regeneration, this test fails and web/API cannot drift.
"""

import json
from pathlib import Path

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi" / "openapi.json"


def test_openapi_matches_checked_in_contract() -> None:
    assert CONTRACT_PATH.exists(), f"missing contract file: {CONTRACT_PATH}"

    checked_in = json.loads(CONTRACT_PATH.read_text())
    live = app.openapi()

    assert live == checked_in, (
        "contracts/openapi/openapi.json is stale — run `make gen-api`"
    )
