/**
 * Status → semantic badge variant. Human-facing labels live in the
 * `employees.status` / `employees.types` message namespaces.
 */

import type { EmployeeStatus } from "../types";

/** Status → semantic badge variant; terminated reads quiet, not alarming. */
export const statusVariant: Record<EmployeeStatus, "success" | "warning" | "muted"> = {
  active: "success",
  on_leave: "warning",
  terminated: "muted",
};
