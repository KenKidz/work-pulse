"""Provision the deterministic synthetic users behind the k6 load gate.

Run with the API venv (the script changes to `apps/api` so the app's `.env`
supplies the database URL):

    uv run python scripts/reset_load_fixtures.py
    uv run python scripts/reset_load_fixtures.py --cleanup

The smoke profile needs more mutation budget than the seeded accounts can
provide: 25 check-ins/s for 60s is 1,500 check-ins (plus a check-out each), so
the gate spreads the load over 50 linked employees. Each synthetic account is
an `employee` except `load-01`, which is a `manager` whose reports are the
other 49 — the board-read half of the profile needs a role the board policy
admits, and a manager board sees all synthetic rows. Mutation budgets are
per user, so 50 users × 120/60s = 6,000 against ~3,000 mutations: the spec's
≥2× headroom rule.

Deterministic and idempotent (fixed ids, fixed emails, `ON CONFLICT` upserts).
Each run hashes a random throwaway password and provisions one opaque session
per fixture; `run.sh` passes the raw tokens to k6 through a protected temp file
so the load profile never logs in (the auth tier has its own integration
tests) and no known credential exists. The accounts are synthetic and
development-only (`.ai/testing.md` rule 6); the script refuses non-dev targets.
`--cleanup` removes the synthetic attendance rows, outbox rows and sessions a
run leaves behind, keeping repeat gates deterministic and leaving the dev
outbox clean for `make check-e2e`.
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from uuid_utils.compat import uuid7

API_DIR = Path(__file__).resolve().parents[1]
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from app.config import get_settings  # noqa: E402
from app.domains.attendance.models import AttendanceRecord  # noqa: E402
from app.domains.employees.models import Employee, Employment  # noqa: E402
from app.domains.identity.credentials import (  # noqa: E402
    hash_password,
    new_session_token,
    session_token_hash,
)
from app.domains.identity.models import Session, User  # noqa: E402
from app.infrastructure.postgres.engine import (  # noqa: E402
    dispose_engine,
    get_session_factory,
)
from app.infrastructure.postgres.models_outbox import EventOutboxRow  # noqa: E402

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
# Seed 001 reference rows (Backend / Software Engineer / Ho Chi Minh Office).
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-0000000d0003")
POSITION_ID = UUID("00000000-0000-4000-8000-0000000e0001")
LOCATION_ID = UUID("00000000-0000-4000-8000-0000000c0001")
HIRED_AT = date(2026, 1, 5)

#: Enough users for ≥2× mutation-budget headroom at 25 check-ins/s. The k6
#: script reads the same `LOAD_USER_COUNT` (default 50).
USER_COUNT = int(os.environ.get("LOAD_USER_COUNT", "50"))


def employee_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-00000001{index:04d}")


def employment_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-00000002{index:04d}")


def user_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-00000003{index:04d}")


def email(index: int) -> str:
    return f"load-{index:02d}@workpulse.test"


def employee_code(index: int) -> str:
    return f"EMP-LOAD{index:02d}"


def _all_employee_ids() -> list[UUID]:
    return [employee_id(i) for i in range(1, USER_COUNT + 1)]


async def _purge_synthetic_facts(session) -> None:
    """Drop attendance/outbox/session rows this gate owns (synthetic only)."""
    ids = _all_employee_ids()
    str_ids = [str(value) for value in ids]
    outbox_employee = EventOutboxRow.payload["data"]["employee_id"].astext

    await session.execute(
        delete(AttendanceRecord)
        .where(AttendanceRecord.tenant_id == TENANT_ID)
        .where(AttendanceRecord.employee_id.in_(ids))
    )
    await session.execute(
        delete(EventOutboxRow)
        .where(EventOutboxRow.tenant_id == TENANT_ID)
        .where(outbox_employee.in_(str_ids))
    )
    await session.execute(
        delete(Session)
        .where(Session.tenant_id == TENANT_ID)
        .where(Session.user_id.in_([user_id(i) for i in range(1, USER_COUNT + 1)]))
    )


def _assert_development_target() -> None:
    """Refuse to create known users or delete synthetic rows outside dev/test."""
    settings = get_settings()
    if settings.environment in {"development", "test"}:
        return
    if os.environ.get("LOAD_FIXTURES_ALLOW_NONDEV") == "1":
        return
    raise SystemExit(
        "reset_load_fixtures: refusing to run against "
        f"environment={settings.environment!r}; set LOAD_FIXTURES_ALLOW_NONDEV=1 "
        "only for a disposable load-test database."
    )


async def provision() -> None:
    """Upsert the synthetic employees/users and clear prior run facts."""
    _assert_development_target()
    settings = get_settings()
    password_hash = await hash_password(secrets.token_urlsafe(32))
    manager = employee_id(1)
    async with get_session_factory()() as session:
        await _purge_synthetic_facts(session)

        for index in range(1, USER_COUNT + 1):
            reports_to = None if index == 1 else manager
            employee = {
                "id": employee_id(index),
                "tenant_id": TENANT_ID,
                "employee_code": employee_code(index),
                "first_name": f"Load{index:02d}",
                "last_name": "Synthetic",
                "email": email(index),
                "phone": None,
                "department_id": DEPARTMENT_ID,
                "position_id": POSITION_ID,
                "location_id": LOCATION_ID,
                "manager_id": reports_to,
                "employment_type": "full_time",
                "status": "active",
                "hired_at": HIRED_AT,
                "terminated_at": None,
            }
            await session.execute(
                pg_insert(Employee)
                .values(**employee)
                .on_conflict_do_update(index_elements=[Employee.id], set_=employee)
            )

        for index in range(1, USER_COUNT + 1):
            account = {
                "id": user_id(index),
                "tenant_id": TENANT_ID,
                "email": email(index),
                "role": "manager" if index == 1 else "employee",
                "employee_id": employee_id(index),
                "password_hash": password_hash,
                "active": True,
            }
            await session.execute(
                pg_insert(User)
                .values(**account)
                .on_conflict_do_update(index_elements=[User.id], set_=account)
            )

        # Rewrite the open period instead of relying on the per-employee
        # partial unique index (an interrupted run may have left another).
        await session.execute(
            delete(Employment)
            .where(Employment.tenant_id == TENANT_ID)
            .where(Employment.employee_id.in_(_all_employee_ids()))
        )
        for index in range(1, USER_COUNT + 1):
            await session.execute(
                pg_insert(Employment).values(
                    id=employment_id(index),
                    tenant_id=TENANT_ID,
                    employee_id=employee_id(index),
                    department_id=DEPARTMENT_ID,
                    position_id=POSITION_ID,
                    location_id=LOCATION_ID,
                    employment_type="full_time",
                    effective_from=HIRED_AT,
                    effective_to=None,
                )
            )

        # Pre-provision one session per fixture. k6 reads the raw tokens from a
        # 0600 temp file; no login traffic, no X-Forwarded-For spoofing and no
        # reusable password are part of the load profile.
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.session_ttl_days)
        session_payload: list[dict[str, str]] = []
        for index in range(1, USER_COUNT + 1):
            token = new_session_token()
            await session.execute(
                pg_insert(Session).values(
                    id=uuid7(),
                    tenant_id=TENANT_ID,
                    user_id=user_id(index),
                    token_hash=session_token_hash(token),
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            session_payload.append({"email": email(index), "token": token})

        await session.commit()

        sessions_file = os.environ.get("LOAD_SESSIONS_FILE")
        if sessions_file:
            target = Path(sessions_file)
            target.write_text(json.dumps({"sessions": session_payload}))
            target.chmod(0o600)


async def cleanup() -> None:
    """Remove the synthetic facts and fixtures (repeatable, dev-clean gates)."""
    _assert_development_target()
    async with get_session_factory()() as session:
        await _purge_synthetic_facts(session)
        ids = _all_employee_ids()
        # Employees/users are fixtures too: leaving 50 active synthetic people
        # in the dev tenant would pollute the e2e lists and board between gate
        # runs. Order follows the foreign keys (sessions went first).
        await session.execute(
            delete(Employment)
            .where(Employment.tenant_id == TENANT_ID)
            .where(Employment.employee_id.in_(ids))
        )
        await session.execute(
            delete(User)
            .where(User.tenant_id == TENANT_ID)
            .where(User.id.in_([user_id(i) for i in range(1, USER_COUNT + 1)]))
        )
        await session.execute(
            delete(Employee)
            .where(Employee.tenant_id == TENANT_ID)
            .where(Employee.id.in_(ids))
        )
        await session.commit()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="only remove the synthetic attendance/outbox/session rows",
    )
    args = parser.parse_args()
    try:
        if args.cleanup:
            await cleanup()
        else:
            await provision()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
