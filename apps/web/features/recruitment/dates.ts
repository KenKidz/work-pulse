/**
 * Display formatting for recruitment data. Timestamps arrive as UTC ISO 8601
 * and render in the browser's local zone through `lib/datetime` (dayjs),
 * never through ad-hoc `new Date(...)` arithmetic.
 */

import { dayjs } from "@/lib/datetime";

/** A UTC ISO timestamp in the browser's local zone. */
export function formatRecruitmentTimestamp(value: string): string {
  return dayjs(value).format("MMM D, YYYY HH:mm");
}

/**
 * A UTC ISO instant as the `YYYY-MM-DDTHH:mm` value a `datetime-local` input
 * expects — the browser's local wall time, not UTC.
 */
export function toLocalDateTimeInput(value: string): string {
  return dayjs(value).format("YYYY-MM-DDTHH:mm");
}

/**
 * A `datetime-local` wall time in the browser's zone → the UTC ISO instant the
 * API contract pins. The browser's zone is the user's intent; the API stores
 * UTC.
 */
export function localDateTimeToUtc(value: string): string {
  return dayjs(value).utc().toISOString();
}

/**
 * The server's current date as a `YYYY-MM-DD` string. Hire-date bounds are
 * evaluated against the API's UTC clock (time-as-a-parameter), so the form
 * uses UTC too instead of the browser zone.
 */
export function todayISO(): string {
  return dayjs.utc().format("YYYY-MM-DD");
}
