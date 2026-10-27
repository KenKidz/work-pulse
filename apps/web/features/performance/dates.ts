/**
 * Display formatting for performance data. Timestamps arrive as UTC ISO 8601
 * and render in the browser's local zone through `lib/datetime`; date-only
 * fields (`due_date`) stay on the UTC calendar so a goal never shifts a day.
 */

import { dayjs } from "@/lib/datetime";

/** A UTC ISO timestamp in the browser's local zone and the active locale. */
export function formatPerformanceTimestamp(value: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** A date-only wire value (`YYYY-MM-DD`) on the UTC calendar. */
export function formatPerformanceDate(value: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

/** A real `YYYY-MM-DD` calendar date (dayjs rolls impossible dates over). */
export function isCalendarDate(value: string): boolean {
  const parsed = dayjs.utc(value);
  return parsed.isValid() && parsed.format("YYYY-MM-DD") === value;
}
