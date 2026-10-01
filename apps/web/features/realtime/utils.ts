/**
 * Pure helpers for the realtime feature: the employee directory (names are
 * joined client-side — frames carry identifiers only) and the wall's
 * working-first ordering.
 */

import { dayjs } from "@/lib/datetime";

import type { Employee, EmployeeSummary, PresenceRow, PresenceRowStatus } from "./types";

export type DirectoryEntry = { name: string; code: string };

export type EmployeeDirectory = Map<string, DirectoryEntry>;

/** Working rows first, then the day's other states; ties by display name. */
const STATUS_ORDER: Record<PresenceRowStatus, number> = {
  present: 0,
  remote: 1,
  checked_out: 2,
  absent: 3,
  on_leave: 4,
};

/**
 * The id → display-name directory. `me` (the session user's linked employee)
 * is merged first because `GET /employees` is reports-only for managers,
 * while the board scope is self + reports — without it the manager's own row
 * could never resolve a name. The list wins on conflicts (same data).
 */
export function buildEmployeeDirectory(
  items: EmployeeSummary[],
  me?: Employee | null,
): EmployeeDirectory {
  const directory: EmployeeDirectory = new Map();
  if (me != null) {
    directory.set(me.id, entryFor(me));
  }
  for (const employee of items) {
    directory.set(employee.id, entryFor(employee));
  }
  return directory;
}

function entryFor(
  employee: Pick<EmployeeSummary, "first_name" | "last_name" | "employee_code">,
): DirectoryEntry {
  return {
    name: `${employee.first_name} ${employee.last_name}`.trim(),
    code: employee.employee_code,
  };
}

/** Display name with a stable identifier fallback — frames are id-only. */
export function employeeDisplayName(employeeId: string, directory: EmployeeDirectory): string {
  const name = directory.get(employeeId)?.name;
  return name !== undefined && name.length > 0 ? name : `Employee …${employeeId.slice(-4)}`;
}

export function employeeCode(employeeId: string, directory: EmployeeDirectory): string | null {
  return directory.get(employeeId)?.code ?? null;
}

export function sortPresenceRows(rows: PresenceRow[], directory: EmployeeDirectory): PresenceRow[] {
  return [...rows].sort((a, b) => {
    const byStatus = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    if (byStatus !== 0) return byStatus;
    return employeeDisplayName(a.employee_id, directory).localeCompare(
      employeeDisplayName(b.employee_id, directory),
    );
  });
}

/** Clock time of a UTC timestamp rendered in the browser's local zone. */
export function formatClockTime(iso: string): string {
  return dayjs(iso).format("HH:mm");
}

/** `minutes_late` as `12m` / `1h 5m` — whole minutes per the contract. */
export function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest}m`;
  if (rest === 0) return `${hours}h`;
  return `${hours}h ${rest}m`;
}
