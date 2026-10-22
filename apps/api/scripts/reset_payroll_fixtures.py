"""Reset the seeded dev tenant's payroll ledger for the e2e gate.

Run with the API venv; the script changes to `apps/api` so the app's `.env` is
used for the database URL:

    uv run python scripts/reset_payroll_fixtures.py

`full-hop.sh` calls this once before Playwright, and the payroll spec calls it
again at the start of its finance test so a Playwright retry (CI has
`retries: 1`) starts from the same clean ledger. Destructive by design:
synthetic dev data only (`.ai/testing.md` rule 6) — never point this at shared
or staging data.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, update

API_DIR = Path(__file__).resolve().parents[1]
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from app.domains.payroll.models import (  # noqa: E402
    PayrollBonus,
    PayrollConfig,
    PayrollDeductionLine,
    PayrollRun,
    PayrollTimesheet,
    Payslip,
    PayslipDeductionLine,
    SalaryRecord,
)
from app.infrastructure.postgres.engine import (  # noqa: E402
    dispose_engine,
    get_session_factory,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")


async def reset() -> None:
    """Drop every payroll row for the seeded tenant, child tables first."""
    async with get_session_factory()() as session:
        # A correction run references the run it supersedes, so clear both
        # self-references before deleting the run history in one pass.
        await session.execute(
            update(PayrollRun)
            .where(PayrollRun.tenant_id == TENANT_ID)
            .values(supersedes_run_id=None, superseded_by_run_id=None)
        )
        for model in (
            PayslipDeductionLine,
            Payslip,
            PayrollTimesheet,
            PayrollBonus,
            PayrollRun,
            SalaryRecord,
            PayrollDeductionLine,
            PayrollConfig,
        ):
            await session.execute(delete(model).where(model.tenant_id == TENANT_ID))
        await session.commit()


async def main() -> None:
    try:
        await reset()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
