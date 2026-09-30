/**
 * Human-facing labels for the identity domain — the wire values are never
 * rendered (pattern from the employees feature).
 *
 * `roleLabel` lives in the auth feature (identity display) and is re-exported
 * here so the users components keep one import path; auth must never import
 * from users.
 */

import type { Role } from "../types";

import { roleLabel } from "@/features/auth/labels";

export { roleLabel };

export const ALL_ROLES = Object.keys(roleLabel) as Role[];

/** Account status label + semantic badge variant (mirrors employees). */
export function accountStatusLabel(active: boolean): string {
  return active ? "Active" : "Inactive";
}

export function accountStatusVariant(active: boolean): "success" | "muted" {
  return active ? "success" : "muted";
}
