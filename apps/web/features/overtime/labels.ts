/**
 * Status tones, role gates and classifiers for overtime. Human-facing labels
 * and stable-code sentences live in the `overtime` message namespace.
 * The role arrays mirror `apps/api/app/domains/overtime/policies.py`: the UI
 * only hides, the API remains the authority.
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

/** Shared role-denied classification for read surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "forbidden");
}

/**
 * A joined employee name from the directory, else the localized fallback the
 * caller supplies (`Employee …a1b2`).
 */
export function overtimeEmployeeLabel(
  employeeId: string,
  directory: ReadonlyMap<string, string>,
  fallback: string,
): string {
  const name = directory.get(employeeId);
  return name !== undefined && name.length > 0 ? name : fallback;
}
