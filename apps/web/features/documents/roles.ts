/**
 * Web-side mirror of the Documents domain's write policy
 * (`apps/api/app/domains/documents/policies.py`). The API remains the
 * authority; the UI only hides controls the caller could not use.
 *
 * Reads are wider (owner, manager over self + direct reports, HR/Admin/Super
 * Admin, Finance own only) and stay enforced by the API — the panel renders a
 * list for whoever the API allows.
 */

import type { Role } from "@workpulse/api-client/index.schemas";

/** Upload, metadata update and delete: HR / Admin / Super Admin only. */
export const DOCUMENT_MANAGE_ROLES: Role[] = ["hr", "admin", "super_admin"];

/** The web-side write gate; the API stays the authority. */
export function canManageDocuments(role: Role): boolean {
  return DOCUMENT_MANAGE_ROLES.includes(role);
}
