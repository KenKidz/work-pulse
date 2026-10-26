/**
 * Human-facing labels for the identity domain — the wire values are never
 * rendered (pattern from the employees feature).
 *
 * `roleLabel` lives in the auth feature (identity display) and is re-exported
 * here so the users components keep one import path; auth must never import
 * from users.
 */

import type { Role } from "../types";

/** The contract-tied role list, mirroring `Role` on the wire. */
export const ALL_ROLES: Role[] = [
  "employee",
  "manager",
  "hr",
  "finance",
  "admin",
  "super_admin",
];

export function accountStatusVariant(active: boolean): "success" | "muted" {
  return active ? "success" : "muted";
}
