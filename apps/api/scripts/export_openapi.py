"""Export the OpenAPI contract to contracts/openapi/openapi.json.

Run from the repo root via `make gen-api`, or from apps/api with:

    uv run python scripts/export_openapi.py

Regenerate after every change to routers or schemas — the web client
(packages/api-client) is generated from this file (ADR 0005).
"""

import json
import sys
from pathlib import Path

# Allow `python scripts/export_openapi.py` from apps/api as well as `-m`.
API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

from app.main import app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT = REPO_ROOT / "contracts" / "openapi" / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
