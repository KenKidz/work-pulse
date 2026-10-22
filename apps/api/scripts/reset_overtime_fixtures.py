"""Reset the seeded dev employees' overtime fixtures for the e2e gate.

Run with the API venv; the script changes to `apps/api` so the app's `.env` is
used for the database URL:

    uv run python scripts/reset_overtime_fixtures.py

`full-hop.sh` calls this once before Playwright, and the overtime and payroll
specs call it again before their flows so a Playwright retry (CI has
`retries: 1`) starts from the same clean slate. An approved or verified claim
has no delete API, so terminal rows from an earlier attempt would otherwise
block the next submit on the same work date (`409 overtime_overlaps` for open
states) or change the payroll totals the seam hop asserts.

Scoped to the two seeded employees that own overtime rows in the suite — the
dev worker (the requester) and the dev manager (the manager-step actor who
also files an own claim for the self-decision refusal). Destructive by design:
synthetic dev data only (`.ai/testing.md` rule 6) — never point this at shared
or staging data.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete

API_DIR = Path(__file__).resolve().parents[1]
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from app.domains.overtime.models import OvertimeRequest  # noqa: E402
from app.infrastructure.postgres.engine import (  # noqa: E402
    dispose_engine,
    get_session_factory,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKER_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000021")
MANAGER_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000023")


async def reset() -> None:
    """Drop the seeded employees' overtime rows for the seeded tenant."""
    async with get_session_factory()() as session:
        await session.execute(
            delete(OvertimeRequest)
            .where(OvertimeRequest.tenant_id == TENANT_ID)
            .where(
                OvertimeRequest.employee_id.in_(
                    (WORKER_EMPLOYEE_ID, MANAGER_EMPLOYEE_ID)
                )
            )
        )
        await session.commit()


async def main() -> None:
    try:
        await reset()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
