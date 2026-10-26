/**
 * Human-facing labels for pinned leave enums — wire values are never rendered
 * (workpulse-ui §6). Badge tones follow the semantic status tokens.
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";
import type { Role } from "@workpulse/api-client/index.schemas";

import type { LeaveRequestStatus } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

/**
 * Web-side gate for the approval inbox (mirrors the API's leave decide
 * policy). Employees and Finance never see the section; the API remains the
 * authority and returns 403 for a direct attempt.
 */
export const LEAVE_APPROVER_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

export const leaveStatusVariant: Record<LeaveRequestStatus, BadgeVariant> = {
  pending: "warning",
  approved: "success",
  rejected: "destructive",
  withdrawn: "muted",
};


