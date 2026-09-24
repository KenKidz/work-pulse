# 3. Payroll is a deterministic simulation, not a compliance engine

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

Payroll is the part of an HRIS that punishes ambition. Real payroll means local
labor law, dynamic tax brackets, multi-region social insurance, statutory
deductions, retroactive corrections, and regulatory reporting — each of which is
a domain of its own, changes by jurisdiction and year, and carries legal
liability when wrong. The project's purpose is engineering depth (event-driven
architecture, workflows, analytics), not tax compliance.

The source brief (`tasks/doc.md` §8) is explicit: do not attempt a production
tax/payroll compliance engine first.

## Decision

Model Payroll as a **deterministic simulation engine**:

```
Net Salary = (Base Salary + Overtime Pay + Bonuses) − Deductions
```

with explicit, inspectable deduction lines (configurable percentages/amounts per
tenant) rather than jurisdiction-aware tax logic. The engineering value is kept
where it is interesting:

- Timesheets are assembled from **approved** attendance and overtime only.
- A payroll run is an asynchronous, idempotent job over a closed period.
- Every run emits `payroll.calculated` / `payslip.issued` events and writes an
  immutable audit entry.
- Re-running a period produces the same result unless its inputs changed.
- Results land in ClickHouse for the "payroll cost by department" analytics.

Regional tax variation, statutory reporting and retroactive adjustments are out
of scope until a documented requirement and its own ADR exist.

## Consequences

- The workflow, event and analytics patterns are exercised fully — the parts
  worth learning — without pretending to be a payroll vendor.
- Deductions stay a transparent list, so the calculation is testable and
  explainable; no hidden jurisdiction tables to maintain.
- The product cannot be sold as compliant payroll, and must not be described
  that way in docs or UI ("simulated payroll" wording).
- If real compliance is ever required, the calculation boundary is already
  isolated in `domains/payroll/`, so an external provider can replace the engine
  instead of the domain.