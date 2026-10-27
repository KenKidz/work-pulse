/**
 * Web-side mirrors of the Performance domain's role sets
 * (`apps/api/app/domains/performance/policies.py`). The API remains the
 * authority; the nav and the workspace gates share these literals so a policy
 * mirror only exists once.
 *
 * Every authenticated role reaches `/performance`: Employee and Finance see
 * their own goals/reviews and the readable skill catalog; Manager runs a team;
 * HR/Admin/Super Admin run the tenant. Finance is deliberately read-only —
 * `GOAL_MANAGE_ROLES` excludes it, mirroring the API's `GOAL_ROLES`.
 */

import type { Role } from "@workpulse/api-client/index.schemas";

/** The nav entry is visible to every authenticated role. */
export const PERFORMANCE_NAV_ROLES: Role[] = [
  "employee",
  "manager",
  "hr",
  "finance",
  "admin",
  "super_admin",
];

/** Goal mutations: self-service (Employee) + team (Manager) + management. */
export const GOAL_MANAGE_ROLES: Role[] = ["employee", "manager", "hr", "admin", "super_admin"];

/** Authoring/reading team reviews: Manager (team) + HR/Admin/Super Admin. */
export const REVIEW_AUTHOR_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

/** The tenant skill catalog is managed by HR/Admin/Super Admin only. */
export const SKILL_CATALOG_ROLES: Role[] = ["hr", "admin", "super_admin"];

/** Employee-skill rows: self-service + team + management. */
export const EMPLOYEE_SKILL_ROLES: Role[] = ["employee", "manager", "hr", "admin", "super_admin"];

/**
 * Nominate providers / read the full feedback set (the review author + HR+).
 * Filing feedback itself is per-review nomination, not a role capability.
 */
export const FEEDBACK_NOMINATE_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

/** Roles whose visibility is self + direct reports or the whole tenant. */
export const PERFORMANCE_TEAM_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

/** Management roles that do not need an employee link to see the tenant. */
export const PERFORMANCE_TENANT_ROLES: Role[] = ["hr", "admin", "super_admin"];
