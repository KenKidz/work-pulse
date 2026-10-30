"""Compare the serving (fold) and CDC report projections after the e2e hop.

T7 proves parity on the real Debezium pipeline: the same fixture is projected
twice — synthetic envelopes folded into the canonical database and OLTP rows
captured through Kafka Connect into the D2 shadow database. This script reads
the four report aggregates through the production repository for one employee
scope and fails unless the two projections are exactly equal and non-empty.

Run with the API venv from `apps/api`; `full-hop.sh` invokes it with the
fixture window and employee id after Playwright finishes. The bounded poll
absorbs pipeline lag (no fixed sleeps), and a fenced shadow answers
`ReportsUnavailableError` until readiness flips.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from clickhouse_connect.driver.client import Client

API_DIR = Path(__file__).resolve().parents[1]
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from app.config import get_settings  # noqa: E402
from app.domains.reports.exceptions import ReportsUnavailableError  # noqa: E402
from app.domains.reports.schemas import ReportQuery  # noqa: E402
from app.infrastructure.clickhouse.client import (  # noqa: E402
    create_clickhouse_client,
)
from app.infrastructure.clickhouse.reports import (  # noqa: E402
    ClickhouseReportsRepository,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
METHODS = (
    "overtime_aggregates",
    "attendance_aggregates",
    "leave_aggregates",
    "headcount_aggregates",
)
_EXPECTED_HOURS = Decimal("2.50")
_EXPECTED_MINUTES = 450
_EXPECTED_DAYS = 2


@dataclass(frozen=True, slots=True)
class Projections:
    """One read of the four report families from one database."""

    overtime: tuple[Any, ...]
    attendance: tuple[Any, ...]
    leave: tuple[Any, ...]
    headcount: tuple[Any, ...]

    def families(self) -> tuple[tuple[Any, ...], ...]:
        return (self.overtime, self.attendance, self.leave, self.headcount)


def _query(args: argparse.Namespace) -> ReportQuery:
    return ReportQuery(
        tenant_id=TENANT_ID,
        start=args.start,
        end=args.end,
        interval="month",
        group_by=("department",),
        employee_scope=(args.employee_id,),
    )


def _collect(client: Client, query: ReportQuery) -> Projections:
    repository = ClickhouseReportsRepository(client)
    values = {method: tuple(getattr(repository, method)(query)) for method in METHODS}
    return Projections(
        overtime=values["overtime_aggregates"],
        attendance=values["attendance_aggregates"],
        leave=values["leave_aggregates"],
        headcount=values["headcount_aggregates"],
    )


def _fixture_present(projections: Projections) -> bool:
    """Both paths must show the fixture's exact metrics, not empty agreement."""
    return (
        any(row.verified_hours == _EXPECTED_HOURS for row in projections.overtime)
        and any(
            row.worked_minutes == _EXPECTED_MINUTES for row in projections.attendance
        )
        and any(row.approved_day_count == _EXPECTED_DAYS for row in projections.leave)
        and any(row.headcount == 1 for row in projections.headcount)
    )


def _diff(serving: Projections, shadow: Projections) -> str:
    return "\n".join(
        f"{method}: serving={serving_rows!r} shadow={shadow_rows!r}"
        for method, serving_rows, shadow_rows in zip(
            METHODS, serving.families(), shadow.families(), strict=True
        )
        if serving_rows != shadow_rows
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--employee-id", type=UUID, required=True)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    serving_client = create_clickhouse_client()
    shadow_database = (settings.cdc_projection_database or "").strip()
    if not shadow_database:
        print(
            "parity: CDC_PROJECTION_DATABASE is empty; the shadow was already cut over",
            file=sys.stderr,
        )
        return 1
    shadow_client = create_clickhouse_client(database=shadow_database)
    query = _query(args)
    deadline = time.monotonic() + args.timeout
    last_problem = "the projections are not ready"
    try:
        while True:
            try:
                serving = _collect(serving_client, query)
                shadow = _collect(shadow_client, query)
            except ReportsUnavailableError as exc:
                last_problem = f"projection unavailable: {exc}"
            else:
                if serving.families() == shadow.families():
                    if _fixture_present(shadow):
                        print(
                            "parity: serving and CDC projections agree "
                            f"({args.start}..{args.end}, employee {args.employee_id})"
                        )
                        return 0
                    last_problem = "the projections agree but the fixture is absent"
                else:
                    last_problem = _diff(serving, shadow)

            if time.monotonic() >= deadline:
                print(f"parity: {last_problem}", file=sys.stderr)
                return 1
            time.sleep(max(0.05, args.poll_seconds))
    finally:
        serving_client.close()
        shadow_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
