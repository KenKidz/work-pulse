/**
 * Date helpers for the attendance feature — dates are `YYYY-MM-DD` strings
 * (pinned contract), timestamps are UTC ISO. The board "today" is computed
 * client-side and sent explicitly (the server default is the UTC date — T1).
 */

import { dayjs } from "@/lib/datetime";

/** Today's date as the `YYYY-MM-DD` string the API contract expects. */
export function todayISO(): string {
  return dayjs().format("YYYY-MM-DD");
}

/** Format an API `date` (`YYYY-MM-DD` or full ISO) in the active locale. */
export function formatDate(value: string, locale = "en-US"): string {
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    ...(dateOnly ? { timeZone: "UTC" } : {}),
  }).format(new Date(dateOnly ? `${value}T00:00:00Z` : value));
}

/** Clock time of a UTC timestamp in the browser's local zone and locale. */
export function formatClockTime(iso: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** `duration_minutes` as `1h 23m` (never negative — floored at close). */
export function formatDuration(
  minutes: number,
  hourShort = "h",
  minuteShort = "m",
): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest}${minuteShort}`;
  if (rest === 0) return `${hours}${hourShort}`;
  return `${hours}${hourShort} ${rest}${minuteShort}`;
}
