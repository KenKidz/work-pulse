"""Read-side as-of dimension attribution for Analytics & Reports (ADR 0024 D3).

The CDC projection stores fact rows without dimension stamps; the report
queries resolve each fact's assignment at read time. These pure helpers are the
single formulation of that rule — the headcount read and the seniority
expression's Python twin use them, and the ClickHouse `ASOF LEFT JOIN` in
`infrastructure/clickhouse/reports.py` reproduces them exactly:

- the covering interval is the latest one with `effective_from <= fact_date`;
- facts that predate the earliest known interval fall back to that interval
  (a pre-extension `employee.created` only knows its first-seen date);
- an employee with no intervals has `unknown` dimensions and is never dropped.

Seniority is measured at the report bucket end; the anniversary itself belongs
to the older band, a Feb 29 anniversary clamps to Feb 28, and missing tenure
degrades to `unknown`.
"""

from collections.abc import Sequence
from datetime import date

from app.domains.reports.schemas import (
    UNKNOWN_SENIORITY_BAND,
    AssignmentRow,
)


def _anniversary(start: date, years: int) -> date:
    """`start + years`, clamping a Feb 29 anniversary to Feb 28."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def seniority_band(hired_at: date | None, at: date | None) -> str:
    """The spec's tenure band for a tenure anchored at `hired_at`.

    Measured at `at` (the row's bucket end; `to` for `interval=total`). Exactly
    one year of tenure is `1_3y`. Missing tenure data degrades to `unknown`.
    """
    if hired_at is None or at is None:
        return UNKNOWN_SENIORITY_BAND
    if at >= _anniversary(hired_at, 5):
        return "5y_plus"
    if at >= _anniversary(hired_at, 3):
        return "3_5y"
    if at >= _anniversary(hired_at, 1):
        return "1_3y"
    return "lt_1y"


def resolve_ordered_assignment(
    assignments: Sequence[AssignmentRow], fact_date: date
) -> AssignmentRow | None:
    """The assignment covering `fact_date` for an ordered interval sequence.

    The headcount read applies this once per employee × bucket over dimension
    rows the repository already returns `ORDER BY effective_from`, so it must
    not re-sort on every visit.
    """
    covering: AssignmentRow | None = None
    for assignment in assignments:
        if assignment.effective_from <= fact_date:
            covering = assignment
        else:
            break
    if covering is None and assignments:
        return assignments[0]
    return covering


__all__ = [
    "resolve_ordered_assignment",
    "seniority_band",
]
