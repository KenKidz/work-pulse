/**
 * Web-side mirror of the Reports domain's role set
 * (`apps/api/app/domains/reports/policies.py`). The API remains the authority;
 * the nav entry and this literal share one source.
 */

import type { Role } from "@workpulse/api-client/index.schemas";

/** View the four reports (managers are scoped by the API). */
export const REPORT_READ_ROLES: Role[] = ["manager", "hr", "finance", "admin", "super_admin"];
