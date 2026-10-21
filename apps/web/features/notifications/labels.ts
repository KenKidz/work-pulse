/**
 * Human-facing labels for the pinned notification kinds — wire values are
 * never rendered (workpulse-ui §6). The v1 trigger map has exactly these five
 * kinds (ADR 0015).
 */

import type { NotificationKind } from "./types";

export const notificationKindLabel: Record<NotificationKind, string> = {
  leave_requested: "Leave request",
  leave_approved: "Leave approved",
  leave_rejected: "Leave rejected",
  leave_withdrawn: "Leave withdrawn",
  attendance_late: "Late check-in",
};
