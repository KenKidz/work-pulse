/**
 * Human-facing labels for pinned enums — the wire values are never rendered.
 */

import type { EmployeeStatus, EmploymentType } from "../types";

export const statusLabel: Record<EmployeeStatus, string> = {
  active: "Active",
  on_leave: "On leave",
  terminated: "Terminated",
};

/** Status → semantic badge variant; terminated reads quiet, not alarming. */
export const statusVariant: Record<EmployeeStatus, "success" | "warning" | "muted"> = {
  active: "success",
  on_leave: "warning",
  terminated: "muted",
};

export const employmentTypeLabel: Record<EmploymentType, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  intern: "Intern",
};
