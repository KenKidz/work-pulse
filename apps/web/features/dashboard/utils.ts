/**
 * Small pure helpers for the dashboard. Dates are `YYYY-MM-DD` strings and
 * timestamps are UTC ISO (the API contract); formatting happens at render time.
 * No fleet-wide dependency: only `dayjs` for "today", `Intl` for the rest.
 */

import { dayjs } from "@/lib/datetime";

export function todayISO(): string {
  return dayjs().format("YYYY-MM-DD");
}

export function currentYear(): number {
  return dayjs().year();
}

/**
 * A bounded window around the browser's today for the self-service open
 * record. The API defines the workday in the employee's location timezone, so
 * looking at only the browser-local date can miss a record around midnight;
 * a short window finds the open record without trusting the client's day.
 */
export function openRecordWindow(today = todayISO()): { from: string; to: string } {
  const date = dayjs(today);
  return {
    from: date.subtract(7, "day").format("YYYY-MM-DD"),
    to: date.add(1, "day").format("YYYY-MM-DD"),
  };
}

/** An API date (`YYYY-MM-DD` or full ISO) in the active locale. */
export function formatDate(value: string, locale = "en-US"): string {
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    ...(dateOnly ? { timeZone: "UTC" } : {}),
  }).format(new Date(dateOnly ? `${value}T00:00:00Z` : value));
}

/** An inclusive leave range — `12–14 Nov` when the month matches, else both. */
export function formatDateRange(start: string, end: string, locale = "en-US"): string {
  if (start === end) return formatDate(start, locale);
  const startLabel = formatDate(start, locale);
  const endLabel = formatDate(end, locale);
  return `${startLabel} – ${endLabel}`;
}

/** Clock time of a UTC timestamp in the browser's local zone and locale. */
export function formatClockTime(iso: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** Overtime hours with up to two decimals, never padded. */
export function formatHours(hours: number, locale = "en-US"): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(hours);
}

/** Initials for the avatar fallback: `Amara Okafor` → `AO`. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

/** Relative age for a queue row (`2h ago`); falls back to the date. */
export function formatRelative(value: string, locale = "en-US", now = Date.now()): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((then - now) / 60_000);
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 7) return formatter.format(days, "day");
  return formatDate(value.slice(0, 10), locale);
}

/** A long human date for the page description — `Tuesday, 4 November`. */
export function formatLongDate(value: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}
