# 17. Money representation and rounding

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Payroll (`.ai/features/payroll.md`, T1) is the first feature that stores and
calculates money in WorkPulse. The repository currently has no `Numeric` column,
no `Decimal` convention and no rounding rule. Left to each task, T2 (columns and
models) and T4 (the calculation core) could drift: `float` columns, rounding at
the total instead of per line, different decimal contexts, or money serialized
as strings — all expensive to reverse once salary history and payslips exist.
The payroll spec pins the formula and "each output line rounded half-up to 2dp";
this ADR pins the representation that makes it testable and deterministic.

Alternatives considered:

- **`float` / `double precision`.** Binary floating point cannot represent most
  cent values exactly, so line sums would not equal totals to the cent and
  "re-running unchanged inputs produces identical payslips" could not be relied
  on. Rejected.
- **Integer minor units.** Exact for storage, but every rate calculation
  (`base / standard_monthly_hours`, percentages) reintroduces a rounding rule
  and the API/ORM would need an encoding convention on every boundary.
  `Numeric` + `Decimal` is the standard fit and keeps the stored value a money
  amount. Rejected.
- **Round only the total.** Payslip lines are individually inspectable in the
  product; line amounts would not add up to the run totals, contradicting an
  acceptance criterion. Rejected.
- **Store or emit formatted money strings.** Loses the numeric type, invites
  parse errors and makes consumers re-implement scale handling. Rejected.

## Decision

1. **`Decimal` only.** Money at rest is PostgreSQL `numeric(12,2)`; in Python it
   is `decimal.Decimal`. No `float` in payroll models, schemas, calculation or
   serializers, and no implicit `float(...)` conversion. SQLAlchemy columns
   declare `Numeric(12, 2)` so asyncpg returns `Decimal`.
2. **One rounding point: the output line.** `base`, `overtime_pay`, `bonuses`
   (the sum of 2dp entries), each deduction line, and `net` are quantized once
   to `Decimal("0.01")` with `ROUND_HALF_UP`. Intermediates — `hourly_rate =
   base / standard_monthly_hours`, `hours × hourly_rate × multiplier`,
   `base × percent / 100` — are never quantized to cents. They are computed
   under a fixed `decimal.localcontext()` with `prec = 28` (Python's default
   precision) and `ROUND_HALF_UP` only as the guard against unbounded division.
   The context is set explicitly so platform defaults can never change a result.
3. **`net` is the formula applied to rounded lines.** `net = base +
   overtime_pay + bonuses − Σ deduction_line.amount`. Because every input to
   that sum is already a 2dp `Decimal`, `net` is exact at 2dp; there is no
   second rounding and no hidden adjustment. Negative net is not clamped: a
   deduction configuration larger than pay is a visible simulation outcome, not
   an error.
4. **Deterministic ordering and rendering.** Deduction lines are ordered by
   `code` before they are written or serialized; every other line has a fixed
   position from the formula. Identical inputs and config produce byte-identical
   serialized payslips (the T4/AC3 determinism test). No timestamps, random
   ids, dict iteration order or locale formatting participate in that payload.
5. **Wire representation is a JSON number.** API responses emit amount fields as
   JSON numbers generated from `Decimal`, never strings, carrying the
   2dp-quantized value. JSON has no scale, so trailing zeros are not guaranteed
   by the wire format; clients format money for display with two decimals. This
   keeps the generated `@workpulse/api-client` typed as `number`.
6. **PII boundary.** Amounts, bonus notes, payslip lines and run totals are PII.
   They never appear in logs, error messages, audit `changes` values, or event
   payloads (`.ai/security.md`; payroll spec → Events).
7. **No rounding helper per feature.** The quantization/context logic lives in
   `domains/payroll/calculation.py` and is the only place allowed to decide
   money scale; repositories and routers only pass `Decimal` through.

## Consequences

- T2's migration/model types and T4's calculation cannot drift: both are pinned
  to `numeric(12,2)` / `Decimal` and line-level half-up rounding.
- Tests assert amounts at the cent, including `Σ payslip lines == run totals`;
  they do not assert intermediate precision beyond the fixed context.
- Consumers and the web client receive numeric money and own display
  formatting; no API string-to-decimal parsing exists.
- A future currency or scale change would be a new ADR and a migration, not a
  quiet change to these rules.
