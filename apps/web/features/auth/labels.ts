/**
 * Identity display helpers. Human-facing role labels live in the `auth.roles`
 * message namespace; this module owns only the derivation that is not copy.
 */

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
