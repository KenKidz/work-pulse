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

/** Format an API `date` (`YYYY-MM-DD` or full ISO) as a display string. */
export function formatDate(value: string): string {
  return dayjs(value).format("MMM D, YYYY");
}

/** Clock time of a UTC timestamp rendered in the browser's local zone. */
export function formatClockTime(iso: string): string {
  return dayjs(iso).format("HH:mm");
}

/** `duration_minutes` as `1h 23m` (never negative — floored at close). */
export function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest}m`;
  if (rest === 0) return `${hours}h`;
  return `${hours}h ${rest}m`;
}
