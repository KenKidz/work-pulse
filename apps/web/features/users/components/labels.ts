/**
 * Human-facing labels for the identity domain — the wire values are never
 * rendered (pattern from the employees feature).
 */

import type { Role } from "../types";

export const roleLabel: Record<Role, string> = {
  employee: "Employee",
  manager: "Manager",
  hr: "HR",
  finance: "Finance",
  admin: "Admin",
  super_admin: "Super admin",
};

export const ALL_ROLES = Object.keys(roleLabel) as Role[];

/** Account status label + semantic badge variant (mirrors employees). */
export function accountStatusLabel(active: boolean): string {
  return active ? "Active" : "Inactive";
}

export function accountStatusVariant(active: boolean): "success" | "muted" {
  return active ? "success" : "muted";
}
