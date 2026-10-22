/**
 * Web-side mirrors of the Payroll domain's role sets
 * (`apps/api/app/domains/payroll/policies.py`). The API remains the authority;
 * the nav, the workspace guard and the read gates share these literals so a
 * policy mirror only exists once.
 */

import type { Role } from "@workpulse/api-client/index.schemas";

/** Configure/run payroll — HR is deliberately view-only. */
export const PAYROLL_OPERATOR_ROLES: Role[] = ["finance", "admin", "super_admin"];

/** Workspace readers: runs, timesheets and every payslip. */
export const PAYROLL_VIEW_ROLES: Role[] = ["hr", "finance", "admin", "super_admin"];
