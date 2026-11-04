import type { Role } from "@workpulse/api-client/index.schemas";

/**
 * Web-side role gates for the attendance surfaces — mirrors
 * `apps/api/app/domains/attendance/policies.py`. The UI only hides; the API
 * remains the authority and returns 403 for a direct attempt.
 */
export const ATTENDANCE_BOARD_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

/** Shift definitions are HR/admin reference data. */
export const SHIFT_MANAGE_ROLES: Role[] = ["hr", "admin", "super_admin"];
