/**
 * Human-facing labels and marks for the identity domain — the wire values are
 * never rendered (pattern from features/users/components/labels.ts, which
 * re-exports `roleLabel` from here: the auth feature owns identity display and
 * the users feature already depends on auth, never the other way around).
 */

import type { Role } from "@workpulse/api-client/index.schemas";

export const roleLabel: Record<Role, string> = {
  employee: "Employee",
  manager: "Manager",
  hr: "HR",
  finance: "Finance",
  admin: "Admin",
  super_admin: "Super admin",
};

/**
 * Avatar initials from the account email: the first letter of up to two words
 * in the local part (`.`/`_`/`-` separated), uppercased. The `User` wire type
 * carries no display name, so this is the only stable mark; empty when nothing
 * is derivable (callers render their own placeholder).
 */
export function initialsFromEmail(email: string): string {
  const local = email.split("@")[0] ?? "";
  const words = local.split(/[._-]+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join("");
}
