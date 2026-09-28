/**
 * Date helpers for the employees feature — Phase 1 dates are `YYYY-MM-DD`
 * strings (pinned contract); `lib/datetime.ts` owns timestamp rendering.
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
