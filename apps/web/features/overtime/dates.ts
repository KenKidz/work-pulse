/**
 * Date and hours helpers for the overtime feature — contract dates are
 * `YYYY-MM-DD` strings and `hours` is a JSON number with at most two decimals
 * (ADR 0017). "Today" is the server's UTC date (the submit rule is evaluated
 * server-side), so the form's `max` value uses UTC, not the browser zone.
 */

import { dayjs } from "@/lib/datetime";

/** The server's current date as the `YYYY-MM-DD` string the API expects. */
export function todayISO(): string {
  return dayjs.utc().format("YYYY-MM-DD");
}

/** Format an API `date` (or a timestamp when the day is enough). */
export function formatDate(value: string): string {
  return dayjs(value).format("MMM D, YYYY");
}

const hoursFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/** Hours with up to two decimals, never padded with zeros. */
export function formatHours(value: number): string {
  return hoursFormatter.format(Number(value));
}
