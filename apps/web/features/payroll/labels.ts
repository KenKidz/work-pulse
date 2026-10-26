/**
 * Status tones, role gates and lifecycle classifiers for payroll. Human-facing
 * labels and stable-code sentences live in the `payroll` message namespace;
 * wire values never reach the DOM (workpulse-ui §6).
 *
 * The role arrays mirror `apps/api/app/domains/payroll/policies.py`: the UI
 * only hides, the API remains the authority.
 */

import type { ComponentProps } from "react";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type { Badge } from "@/components/ui/badge";

import type { PayrollDeductionKind, PayrollRunStatus, PayslipStatus, Role } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

export { PAYROLL_OPERATOR_ROLES, PAYROLL_VIEW_ROLES } from "./roles";

/**
 * Roles that may read the employee directory. Payroll shows names when the
 * signed-in role can read employees and falls back to a stable id tail
 * otherwise — the employee-list policy still decides.
 */
export const EMPLOYEE_DIRECTORY_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

export const payrollRunStatusVariant: Record<PayrollRunStatus, BadgeVariant> = {
  queued: "muted",
  calculating: "warning",
  calculated: "secondary",
  finalized: "success",
  failed: "destructive",
};

export const payslipStatusVariant: Record<PayslipStatus, BadgeVariant> = {
  provisional: "warning",
  issued: "success",
  superseded: "muted",
};

export const DEDUCTION_KINDS: PayrollDeductionKind[] = ["percent_of_base", "fixed_amount"];

/** A running job should be polled; a settled run should not. */
export function isRunInProgress(status: PayrollRunStatus): boolean {
  return status === "queued" || status === "calculating";
}

/** Finalize is allowed only from `calculated` (AC14's disabled state). */
export function canFinalizeRun(status: PayrollRunStatus): boolean {
  return status === "calculated";
}

export function canRecalculateRun(status: PayrollRunStatus): boolean {
  return status === "calculated" || status === "failed";
}

/** Bonuses need an existing payslip set and a non-finalized run. */
export function canAddBonus(status: PayrollRunStatus): boolean {
  return status === "calculated";
}

export function isPayrollNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** A failed run's summary is still readable; the empty tables wait for a recalc. */
export function isRunCalculated(status: PayrollRunStatus): boolean {
  return status === "calculated" || status === "finalized";
}
