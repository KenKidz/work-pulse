/**
 * Human-facing labels, status tones, role gates and pinned error messages for
 * overtime — wire values and stable codes are never rendered (workpulse-ui
 * §6). The role arrays mirror `apps/api/app/domains/overtime/policies.py`: the
 * UI only hides, the API remains the authority.
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type { OvertimeRequestStatus, Role } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

/**
 * Web-side gates for the two decision surfaces (mirror the API's step
 * policies). Employees and Finance never see an inbox; the API still returns
 * `403` for a direct attempt.
 */
export const OVERTIME_MANAGER_STEP_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];
export const OVERTIME_HR_STEP_ROLES: Role[] = ["hr", "admin", "super_admin"];

export const overtimeStatusLabel: Record<OvertimeRequestStatus, string> = {
  requested: "Requested",
  manager_approved: "Manager approved",
  hr_verified: "HR verified",
  included_in_payroll: "Included in payroll",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const overtimeStatusVariant: Record<OvertimeRequestStatus, BadgeVariant> = {
  requested: "warning",
  manager_approved: "secondary",
  hr_verified: "success",
  included_in_payroll: "muted",
  rejected: "destructive",
  withdrawn: "muted",
};

/** States where a peer decision is pending — polled at the fast cadence. */
export function isOvertimeDeciding(status: OvertimeRequestStatus): boolean {
  return status === "requested" || status === "manager_approved";
}

/**
 * Payroll-bound states — inclusion is a monthly accounting flip with no event,
 * so a list holding one is refreshed at the slow cadence, not the decision one.
 */
export function isOvertimePayrollBound(status: OvertimeRequestStatus): boolean {
  return status === "hr_verified";
}

/** Withdrawal is own-only and pre-verification (PO Decision 4). */
export function canWithdraw(status: OvertimeRequestStatus): boolean {
  return status === "requested" || status === "manager_approved";
}

/**
 * Stable code → sentence. Every pinned submit/decision/withdraw code has an
 * entry so no raw `overtime_*` or `employee_*` code reaches a toast or alert.
 */
export const OVERTIME_ERROR_MESSAGES: Record<string, string> = {
  overtime_overlaps: "You already have an open overtime request for that work date.",
  overtime_work_date_in_future:
    "Overtime is claimed for hours already worked. Choose today or an earlier date.",
  overtime_not_requested:
    "This request is no longer waiting for manager approval. Refresh and try again.",
  overtime_not_manager_approved:
    "This request is no longer waiting for HR verification. Refresh and try again.",
  overtime_not_open: "This request can no longer be withdrawn or rejected. Refresh and try again.",
  employee_not_active: "Your employee record isn’t active, so overtime can’t be claimed.",
  overtime_request_not_found: "This overtime request is no longer available.",
  employee_not_found: "No employee record is linked to your account.",
  validation_error: "Check the highlighted fields and try again.",
  forbidden: "You don’t have permission to do that.",
};

/** A stable, user-facing sentence for an overtime failure (never a raw code). */
export function overtimeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const mapped = error.code === null ? undefined : OVERTIME_ERROR_MESSAGES[error.code];
    if (mapped !== undefined) return mapped;
    if (error.status === 403) return OVERTIME_ERROR_MESSAGES.forbidden ?? "Forbidden.";
    if (error.status === 404) {
      return OVERTIME_ERROR_MESSAGES.overtime_request_not_found ?? "Overtime request not found.";
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

/** Shared role-denied classification for read surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "forbidden");
}

/**
 * A joined employee name from the directory, else the app-wide id-tail
 * convention (`Employee …a1b2`) — the users/leave fallback.
 */
export function overtimeEmployeeLabel(
  employeeId: string,
  directory: ReadonlyMap<string, string>,
): string {
  const name = directory.get(employeeId);
  return name !== undefined && name.length > 0 ? name : `Employee …${employeeId.slice(-4)}`;
}
