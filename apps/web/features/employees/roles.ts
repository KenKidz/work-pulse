/**
 * Web-side mirrors of the Employees domain's role sets
 * (`apps/api/app/domains/employees/policies.py`). The API remains the
 * authority; the nav entry, the workspace guard and the create action share
 * these literals so a policy mirror only exists once.
 */

import type { Role } from "@workpulse/api-client/index.schemas";

/** List/read employees: managers see their direct reports; HR+ the tenant. */
export const EMPLOYEE_READ_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

/** Create/update/transfer/terminate: HR/Admin/Super Admin only. */
export const EMPLOYEE_MANAGE_ROLES: Role[] = ["hr", "admin", "super_admin"];
