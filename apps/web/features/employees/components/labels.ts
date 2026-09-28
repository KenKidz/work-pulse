/**
 * Human-facing labels for pinned enums — the wire values are never rendered.
 */

import type { EmployeeStatus, EmploymentType } from "../types";

export const statusLabel: Record<EmployeeStatus, string> = {
  active: "Active",
  on_leave: "On leave",
  terminated: "Terminated",
};

export const employmentTypeLabel: Record<EmploymentType, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  intern: "Intern",
};
