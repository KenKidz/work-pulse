"""Reset the seeded dev tenant's performance fixtures and ensure the e2e peer.

Run with the API venv; the script changes to `apps/api` so the app's `.env` is
used for the database URL:

    uv run python scripts/reset_performance_fixtures.py

`full-hop.sh` calls this once before Playwright, and the performance spec calls
it again before its flow so a Playwright retry (CI has `retries: 1`) starts
from the same clean slate. Goals, reviews, feedback and the skill catalog are
mutable through the UI, and a repeat run would otherwise leave earlier drafts,
feedback rows or catalog names behind (the skill name is unique per tenant, and
a provider can file only once per review).

The feedback hop also needs a third active employee who is neither the review
subject (Wendy, `...0021`) nor its author (Morgan, `...0023`), and that employee
needs a login to file the peer comment. The dev seeds only link Wendy and
Morgan to accounts, so this script upserts a deterministic peer employee
("Parker Peer", reporting to Morgan so the manager's nomination picker and the
peer's own employee scope both include them) and its linked employee user with
the same synthetic dev password as seed 003. The account is synthetic and
dev-only — never reuse this password or fixture outside local work.

Deletion order follows the foreign keys: feedback → providers → reviews →
goals, then employee skills → catalog skills. Scoped to the seeded dev tenant
and destructive by design: synthetic dev data only (`.ai/testing.md` rule 6) —
never point this at shared or staging data.
"""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

API_DIR = Path(__file__).resolve().parents[1]
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from app.domains.employees.models import Employee, Employment  # noqa: E402
from app.domains.identity.credentials import hash_password  # noqa: E402
from app.domains.identity.models import User  # noqa: E402
from app.domains.performance.models import (  # noqa: E402
    EmployeeSkill,
    Goal,
    Review,
    ReviewFeedback,
    ReviewProvider,
    Skill,
)
from app.infrastructure.postgres.engine import (  # noqa: E402
    dispose_engine,
    get_session_factory,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
MANAGER_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000023")

# The deterministic peer the feedback flow nominates (seed 003's next free
# block: employees 0021/0023 and employments 0022/0024 are taken).
PEER_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000031")
PEER_EMPLOYMENT_ID = UUID("00000000-0000-4000-8000-000000000032")
PEER_USER_ID = UUID("00000000-0000-4000-8000-000000000033")
PEER_EMAIL = "peer@workpulse.test"
PEER_EMPLOYEE_CODE = "EMP-DEV03"
PEER_FIRST_NAME = "Parker"
PEER_LAST_NAME = "Peer"

# Seed 001 reference rows (Backend / Software Engineer / Ho Chi Minh Office).
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-0000000d0003")
POSITION_ID = UUID("00000000-0000-4000-8000-0000000e0001")
LOCATION_ID = UUID("00000000-0000-4000-8000-0000000c0001")
HIRED_AT = date(2026, 1, 5)

# Seed 003's shared dev password (synthetic and dev-only). Hash it here rather
# than copying the seed's argon2 literal: the peer must log in with the same
# password as every seeded account, and a copied hash would silently drift if
# that password ever changes.
DEV_PASSWORD = "workpulse-dev"


async def reset() -> None:
    """Drop the seeded tenant's performance rows, then ensure the peer login."""
    password_hash = await hash_password(DEV_PASSWORD)
    async with get_session_factory()() as session:
        await session.execute(
            delete(ReviewFeedback).where(ReviewFeedback.tenant_id == TENANT_ID)
        )
        await session.execute(
            delete(ReviewProvider).where(ReviewProvider.tenant_id == TENANT_ID)
        )
        await session.execute(delete(Review).where(Review.tenant_id == TENANT_ID))
        await session.execute(delete(Goal).where(Goal.tenant_id == TENANT_ID))
        await session.execute(
            delete(EmployeeSkill).where(EmployeeSkill.tenant_id == TENANT_ID)
        )
        await session.execute(delete(Skill).where(Skill.tenant_id == TENANT_ID))
        await session.commit()

        await session.execute(
            pg_insert(Employee)
            .values(
                id=PEER_EMPLOYEE_ID,
                tenant_id=TENANT_ID,
                employee_code=PEER_EMPLOYEE_CODE,
                first_name=PEER_FIRST_NAME,
                last_name=PEER_LAST_NAME,
                email=PEER_EMAIL,
                phone=None,
                department_id=DEPARTMENT_ID,
                position_id=POSITION_ID,
                location_id=LOCATION_ID,
                manager_id=MANAGER_EMPLOYEE_ID,
                employment_type="full_time",
                status="active",
                hired_at=HIRED_AT,
                terminated_at=None,
            )
            .on_conflict_do_update(
                index_elements=[Employee.id],
                set_={
                    "employee_code": PEER_EMPLOYEE_CODE,
                    "first_name": PEER_FIRST_NAME,
                    "last_name": PEER_LAST_NAME,
                    "email": PEER_EMAIL,
                    "department_id": DEPARTMENT_ID,
                    "position_id": POSITION_ID,
                    "location_id": LOCATION_ID,
                    "manager_id": MANAGER_EMPLOYEE_ID,
                    "employment_type": "full_time",
                    "status": "active",
                    "hired_at": HIRED_AT,
                    "terminated_at": None,
                },
            )
        )
        # Rewrite the open period instead of relying on the per-employee
        # partial unique index: an interrupted manual experiment may have left
        # a different period row behind.
        await session.execute(
            delete(Employment)
            .where(Employment.tenant_id == TENANT_ID)
            .where(Employment.employee_id == PEER_EMPLOYEE_ID)
        )
        await session.execute(
            pg_insert(Employment).values(
                id=PEER_EMPLOYMENT_ID,
                tenant_id=TENANT_ID,
                employee_id=PEER_EMPLOYEE_ID,
                department_id=DEPARTMENT_ID,
                position_id=POSITION_ID,
                location_id=LOCATION_ID,
                employment_type="full_time",
                effective_from=HIRED_AT,
                effective_to=None,
            )
        )
        await session.execute(
            pg_insert(User)
            .values(
                id=PEER_USER_ID,
                tenant_id=TENANT_ID,
                email=PEER_EMAIL,
                role="employee",
                employee_id=PEER_EMPLOYEE_ID,
                password_hash=password_hash,
                active=True,
            )
            .on_conflict_do_update(
                index_elements=[User.id],
                set_={
                    "email": PEER_EMAIL,
                    "role": "employee",
                    "employee_id": PEER_EMPLOYEE_ID,
                    "password_hash": password_hash,
                    "active": True,
                },
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
