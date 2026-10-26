/**
 * Web-side mirrors of the Recruitment domain's role sets
 * (`apps/api/app/domains/recruitment/policies.py`). The API remains the
 * authority; the nav, the workspace guard and the tab gates share these
 * literals so a policy mirror only exists once.
 */

import type { Role } from "@workpulse/api-client/index.schemas";

/**
 * Run recruitment — jobs, the candidate pipeline, interviews and hiring are
 * HR / Admin / Super Admin only (the security matrix's *Run recruitment* row).
 */
export const RECRUITMENT_MANAGER_ROLES: Role[] = ["hr", "admin", "super_admin"];

/**
 * The nav and workspace gate. Managers reach the assigned-interview view;
 * HR, Admin and Super Admin reach the management tabs. Employees and Finance
 * have no standing on the web surface (the API refuses management endpoints
 * with `403`); an assigned interviewer outside these roles is an API-only
 * capability in v1.
 */
export const RECRUITMENT_NAV_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];
