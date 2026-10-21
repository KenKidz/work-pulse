/**
 * Human-facing labels, status tones, role gates and pinned error messages for
 * payroll — wire values and stable codes are never rendered (workpulse-ui §6).
 *
 * The role arrays mirror `apps/api/app/domains/payroll/policies.py`: the UI
 * only hides, the API remains the authority. `payrollErrorMessage` is the one
 * place a stable code becomes a sentence, so every toast and inline error
 * shares the same wording and no `payroll_*` code can leak into the interface.
 */

import type { ComponentProps } from "react";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type { Badge } from "@/components/ui/badge";

import type {
  PayrollDeductionKind,
  PayrollRunStatus,
  PayrollRunTotals,
  PayslipStatus,
  Role,
} from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

/** Configure/run payroll — HR is deliberately view-only. */
export const PAYROLL_OPERATOR_ROLES: Role[] = ["finance", "admin", "super_admin"];

/** Workspace readers: runs, timesheets and every payslip. */
export const PAYROLL_VIEW_ROLES: Role[] = ["hr", "finance", "admin", "super_admin"];

/**
 * Roles that may read the employee directory. Payroll shows names when the
 * signed-in role can read employees and falls back to a stable id tail
 * otherwise — the employee-list policy still decides.
 */
export const EMPLOYEE_DIRECTORY_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

export const payrollRunStatusLabel: Record<PayrollRunStatus, string> = {
  queued: "Queued",
  calculating: "Calculating",
  calculated: "Calculated",
  finalized: "Finalized",
  failed: "Failed",
};

export const payrollRunStatusVariant: Record<PayrollRunStatus, BadgeVariant> = {
  queued: "muted",
  calculating: "warning",
  calculated: "secondary",
  finalized: "success",
  failed: "destructive",
};

export const payslipStatusLabel: Record<PayslipStatus, string> = {
  provisional: "Provisional",
  issued: "Issued",
  superseded: "Superseded",
};

export const payslipStatusVariant: Record<PayslipStatus, BadgeVariant> = {
  provisional: "warning",
  issued: "success",
  superseded: "muted",
};

export const deductionKindLabel: Record<PayrollDeductionKind, string> = {
  percent_of_base: "Percent of base",
  fixed_amount: "Fixed amount",
};

export const payrollTotalsLabel: Record<keyof PayrollRunTotals, string> = {
  base: "Base pay",
  overtime_pay: "Overtime pay",
  bonuses: "Bonuses",
  deductions: "Deductions",
  net: "Net pay",
};

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

/**
 * Stable code → sentence. The six `409` lifecycle codes are pinned by the
 * feature spec; the rest cover the same surface's validation and not-found
 * paths so no raw code reaches a toast.
 */
export const PAYROLL_ERROR_MESSAGES: Record<string, string> = {
  payroll_period_not_closed: "This month hasn’t ended yet. Choose a month that has already closed.",
  payroll_period_open: "A run for this month is still open. Finalize it before starting another.",
  payroll_run_not_calculated: "Calculate this run before finalizing it.",
  payroll_run_finalized: "This run is finalized and can no longer change.",
  payroll_employee_not_in_run: "That employee isn’t part of this run, so no bonus can be added.",
  salary_effective_date_taken: "This employee already has a salary record effective that date.",
  payroll_deduction_code_taken: "That deduction code is already in use.",
  payroll_deduction_not_found: "This deduction line is no longer available.",
  payroll_run_stale: "The run’s inputs changed. Recalculate before finalizing.",
  payroll_run_not_found: "This payroll run is no longer available.",
  payroll_not_found: "This payroll record is no longer available.",
  payslip_not_found: "This payslip is no longer available.",
  employee_not_found: "That employee doesn’t exist in this organization.",
  validation_error: "Check the highlighted fields and try again.",
  forbidden: "You don’t have permission to do that.",
};

/** A stable, user-facing sentence for a payroll failure (never a raw code). */
export function payrollErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const mapped = error.code === null ? undefined : PAYROLL_ERROR_MESSAGES[error.code];
    if (mapped !== undefined) return mapped;
    if (error.status === 403) return PAYROLL_ERROR_MESSAGES.forbidden ?? "Forbidden.";
    if (error.status === 404) {
      return PAYROLL_ERROR_MESSAGES.payroll_not_found ?? "Payroll record not found.";
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

export function isPayrollNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * The run carries a stable `failed_code`, never a raw exception. Show a
 * recovery sentence with no internals; recalculate is the only next step.
 */
export function payrollRunFailureMessage(failedCode: string | null | undefined): string {
  switch (failedCode) {
    case "payroll_calculation_failed":
      return "The calculation job failed. Recalculate the run to try again.";
    default:
      return "The calculation didn’t finish. Recalculate the run to try again.";
  }
}

/** A failed run's summary is still readable; the empty tables wait for a recalc. */
export function isRunCalculated(status: PayrollRunStatus): boolean {
  return status === "calculated" || status === "finalized";
}
