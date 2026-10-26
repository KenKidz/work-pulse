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
export function formatDate(value: string, locale = "en-US"): string {
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    ...(dateOnly ? { timeZone: "UTC" } : {}),
  }).format(new Date(dateOnly ? `${value}T00:00:00Z` : value));
}

const hoursFormatters = new Map<string, Intl.NumberFormat>();

/** Hours with up to two decimals, never padded with zeros. */
export function formatHours(value: number, locale = "en-US"): string {
  const cached = hoursFormatters.get(locale);
  if (cached !== undefined) return cached.format(Number(value));
  const formatter = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
  hoursFormatters.set(locale, formatter);
  return formatter.format(Number(value));
}
