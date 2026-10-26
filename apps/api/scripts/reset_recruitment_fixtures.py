"""Reset the seeded dev tenant's recruitment fixtures for the e2e gate.

Run with the API venv; the script changes to `apps/api` so the app's `.env` is
used for the database URL:

    uv run python scripts/reset_recruitment_fixtures.py

`full-hop.sh` calls this once before Playwright, and the recruitment spec calls
it again before its flow so a Playwright retry (CI has `retries: 1`) starts from
the same clean slate. Jobs, candidates and interviews are mutable through the
UI, and a hire creates an Employee (plus its first employment period) through
the Employees seam; a repeat run would otherwise collide on the unique
`(tenant_id, job_id, email)` candidate constraint and the employees' email/code
uniqueness.

Deletion order follows the foreign keys: interviews → candidates → jobs, then
the employments and employees created by the hires (candidate `employee_id`
captures them before the candidates go). Scoped to the seeded dev tenant and
destructive by design: synthetic dev data only (`.ai/testing.md` rule 6) —
never point this at shared or staging data.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select

API_DIR = Path(__file__).resolve().parents[1]
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from app.domains.employees.models import Employee, Employment  # noqa: E402
from app.domains.recruitment.models import (  # noqa: E402
    Candidate,
    Interview,
    Job,
)
from app.infrastructure.postgres.engine import (  # noqa: E402
    dispose_engine,
    get_session_factory,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")


async def reset() -> None:
    """Drop the seeded tenant's recruitment rows and the hires' employees."""
    async with get_session_factory()() as session:
        hired_employee_ids = (
            await session.scalars(
                select(Candidate.employee_id)
                .where(Candidate.tenant_id == TENANT_ID)
                .where(Candidate.employee_id.is_not(None))
            )
        ).all()

        await session.execute(delete(Interview).where(Interview.tenant_id == TENANT_ID))
        await session.execute(delete(Candidate).where(Candidate.tenant_id == TENANT_ID))
        await session.execute(delete(Job).where(Job.tenant_id == TENANT_ID))

        if hired_employee_ids:
            # Employment periods reference the employee; delete both before the
            # rows the hire wrote (the candidate rows that pointed at them are
            # already gone).
            await session.execute(
                delete(Employment)
                .where(Employment.tenant_id == TENANT_ID)
                .where(Employment.employee_id.in_(hired_employee_ids))
            )
            await session.execute(
                delete(Employee)
                .where(Employee.tenant_id == TENANT_ID)
                .where(Employee.id.in_(hired_employee_ids))
            )
        await session.commit()


async def main() -> None:
    try:
        await reset()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
