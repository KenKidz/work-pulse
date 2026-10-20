/**
 * Date helpers for the leave feature — contract dates are `YYYY-MM-DD`
 * strings. "Today" is the server's UTC date (the submit rule is evaluated
 * server-side), so the form's `min` values use UTC, not the browser zone.
 */

import { dayjs } from "@/lib/datetime";

import type { LeaveTypeCode } from "./types";

/** The server's current date as the `YYYY-MM-DD` string the API expects. */
export function todayISO(): string {
  return dayjs.utc().format("YYYY-MM-DD");
}

/** `annual`/`unpaid` start today or later; `sick` may start 7 days back. */
export function earliestStartDate(code: LeaveTypeCode | undefined): string {
  return code === "sick" ? dayjs.utc().subtract(7, "day").format("YYYY-MM-DD") : todayISO();
}

/** Format an API `date` (or timestamp when a display date is enough). */
export function formatDate(value: string): string {
  return dayjs(value).format("MMM D, YYYY");
}

/** Inclusive whole-day span, or `null` until both dates parse and order. */
export function inclusiveDays(start: string, end: string): number | null {
  if (!start || !end || end < start) return null;
  return dayjs(end).diff(dayjs(start), "day") + 1;
}
