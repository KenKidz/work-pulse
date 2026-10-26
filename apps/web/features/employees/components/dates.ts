/**
 * Date helpers for the employees feature — Phase 1 dates are `YYYY-MM-DD`
 * strings (pinned contract); `lib/datetime.ts` owns timestamp rendering.
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
