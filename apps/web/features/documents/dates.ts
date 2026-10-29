/**
 * Display helpers for document dates. `expiry_date` is a calendar date
 * (`YYYY-MM-DD`) and stays on the UTC calendar so it never shifts a day;
 * timestamps arrive as UTC ISO 8601 and render in the browser's local zone.
 */

import { dayjs } from "@/lib/datetime";

/** A real `YYYY-MM-DD` calendar date (dayjs rolls impossible dates over). */
export function isCalendarDate(value: string): boolean {
  const parsed = dayjs.utc(value);
  return parsed.isValid() && parsed.format("YYYY-MM-DD") === value;
}

/** A date-only wire value (`YYYY-MM-DD`) on the UTC calendar. */
export function formatDocumentDate(value: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

/** A UTC ISO timestamp in the browser's local zone and the active locale. */
export function formatDocumentTimestamp(value: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

/**
 * Whether a stored expiry date is already in the past. The comparison is on
 * calendar dates: a document expiring today is still valid today.
 */
export function isExpiredDate(value: string | null | undefined, today = dayjs()): boolean {
  if (value === null || value === undefined || value === "") return false;
  return value < today.format("YYYY-MM-DD");
}
